"""
core/inspect_worker.py - High-speed parallel multi-tier media inspection worker for Instagram.
Features chained multi-tier pagination, detailed system diagnostics, unauthenticated-first routing,
adaptive Gaussian jitter pacing, and anti-scraping checkpoint circuit-breakers.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import logging
import os
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Set

from PyQt6.QtCore import QThread, pyqtSignal

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from config.constants import (
        DEFAULT_HEADERS,
        DEFAULT_PAGE_SIZE,
        DEFAULT_REQUEST_TIMEOUT,
        DEFAULT_USER_AGENT,
        IG_APP_ID,
        IG_BASE_URL,
        IG_CLIPS_USER_URL,
        IG_FEED_USER_URL,
        IG_USER_INFO_MOBILE_URL,
        IG_WEB_PROFILE_INFO_URL,
        MAX_PAGINATION_PAGES,
        MOBILE_USER_AGENT,
        USER_AGENT,
    )
except ImportError:
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "129477",
        "X-Requested-With": "XMLHttpRequest",
    }
    DEFAULT_PAGE_SIZE = 24
    DEFAULT_REQUEST_TIMEOUT = 12
    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    MOBILE_USER_AGENT = "Instagram 300.0.0.29.110 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100)"
    USER_AGENT = DEFAULT_USER_AGENT
    IG_BASE_URL = "https://www.instagram.com"
    IG_APP_ID = "936619743392459"
    IG_WEB_PROFILE_INFO_URL = (
        "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    )
    IG_FEED_USER_URL = "https://www.instagram.com/api/v1/feed/user/{user_id}/"
    IG_CLIPS_USER_URL = "https://www.instagram.com/api/v1/clips/user/"
    IG_USER_INFO_MOBILE_URL = "https://i.instagram.com/api/v1/users/{user_id}/info/"
    MAX_PAGINATION_PAGES = 15

# --- Anti-Scraping Protection & Pacing Defaults ---
DEFAULT_MAX_ITEMS_PER_PROFILE = 36  # Safe default threshold (~3 grid pages)
PROFILE_PAGING_MEAN_DELAY = 2.8  # Gaussian mean delay (seconds)
PROFILE_PAGING_STD_DEV = 0.5  # Gaussian standard deviation
MIN_PAGING_DELAY = 1.8  # Lower bound floor for pagination
MAX_PAGING_DELAY = 5.0  # Upper bound ceiling for pagination

INTER_TARGET_COOLDOWN_MIN = 10.0  # Rest cooldown between distinct targets (seconds)
INTER_TARGET_COOLDOWN_MAX = 18.0

from core.parser import (
    is_standalone_video,
    normalize_url,
    parse_instagram_url,
    shortcode_to_id,
)

from core.client_engine import ResilientSession
from core.parser import (
    NormalizedMedia,
    UnifiedInstagramParser,
    is_standalone_video,
    normalize_url,
    parse_instagram_url,
    shortcode_to_id,
)

logger = logging.getLogger("InspectWorker")


class InstagramReelsResolver:
    """Handles multi-tier Reels querying with persisted doc_id failover

    and public timeline fallback.
    """

    # Primary: Modern Polaris Reels Tab Root Query
    DOC_ID_REELS_PRIMARY = "7423376721066795"
    FRIENDLY_NAME_REELS = "PolarisProfileReelsTabRootQuery"

    # Secondary: Modern Profile Posts/Timeline (Accessible anonymously)
    DOC_ID_POSTS_TIMELINE = "6915638531862590"
    FRIENDLY_NAME_POSTS = "PolarisProfilePostsQuery"

    def __init__(self, session: ResilientSession) -> None:
        self.session = session

    def fetch_user_reels(
        self,
        target_user_id: str,
        target_username: str,
        max_items: int = 12,
        cursor: str | None = None,
    ) -> list[NormalizedMedia]:
        results: list[NormalizedMedia] = []

        # -------------------------------------------------------------
        # Tier 2: Dedicated GraphQL Clips Connection
        # -------------------------------------------------------------
        # Meta schema requires target_user_id as string inside data envelope
        variables_nested = {
            "data": {
                "include_feed_video": True,
                "page_size": max_items,
                "target_user_id": str(target_user_id),
            },
            "after": cursor,
            "first": max_items,
        }

        try:
            logger.debug(
                "[InspectWorker] Executing %s for user %s",
                self.FRIENDLY_NAME_REELS,
                target_user_id,
            )
            data = self.session.execute_persisted_query(
                doc_id=self.DOC_ID_REELS_PRIMARY,
                variables=variables_nested,
                friendly_name=self.FRIENDLY_NAME_REELS,
            )

            clips_connection = data.get("data", {}).get(
                "xdt_api__v1__clips__user__connection_v2", {}
            )
            edges = (
                clips_connection.get("edges")
                if isinstance(clips_connection, dict)
                else None
            )

            if isinstance(edges, list) and len(edges) > 0:
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    node = edge.get("node")
                    if not isinstance(node, dict):
                        continue
                    # Clips edges wrap the media entity under 'media'
                    media_payload = (
                        node.get("media")
                        if isinstance(node.get("media"), dict)
                        else node
                    )
                    normalized = UnifiedInstagramParser.parse_graphql_node(
                        media_payload
                    )
                    if normalized:
                        results.append(normalized)

                if results:
                    return results

        except Exception as exc:
            logger.debug(
                "[InspectWorker] [GraphQLReelsTab] Failed (%s). Falling back to Timeline stream.",
                exc,
            )

        # -------------------------------------------------------------
        # Tier 2.1 Fallback: Query Profile Posts and Filter for Videos
        # -------------------------------------------------------------
        variables_timeline = {
            "after": cursor,
            "first": max_items * 2,  # Over-fetch to account for image post filtering
            "id": str(target_user_id),
        }

        try:
            logger.debug(
                "[InspectWorker] Executing %s fallback for user %s",
                self.FRIENDLY_NAME_POSTS,
                target_user_id,
            )
            timeline_data = self.session.execute_persisted_query(
                doc_id=self.DOC_ID_POSTS_TIMELINE,
                variables=variables_timeline,
                friendly_name=self.FRIENDLY_NAME_POSTS,
            )

            data_root = timeline_data.get("data", {})
            user_node = data_root.get(
                "xdt_api__v1__feed__timeline__connection_v2"
            ) or data_root.get("user", {}).get("edge_owner_to_timeline_media")

            edges = user_node.get("edges") if isinstance(user_node, dict) else None
            if isinstance(edges, list):
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    node = edge.get("node")
                    if not isinstance(node, dict):
                        continue

                    # Filter strictly for video entities (Reels / Clips / Video posts)
                    is_video = bool(node.get("is_video") or node.get("media_type") == 2)
                    if is_video:
                        normalized = UnifiedInstagramParser.parse_graphql_node(node)
                        if normalized:
                            results.append(normalized)

        except Exception as exc:
            logger.warning("[InspectWorker] [GraphQLTimelineFallback] Failed: %s", exc)

        return results


class YTDLPQuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass


class InspectWorker(QThread):
    progress = pyqtSignal(int)
    item_found = pyqtSignal(dict)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)
    media_found = pyqtSignal(dict)
    inspection_finished = pyqtSignal(int)
    error_occurred = pyqtSignal(str)

    MAX_CONCURRENT_INSPECTS = 1

    def __init__(
        self,
        targets: List[str],
        cookie_str: Optional[str] = None,
        cookie_file: Optional[str] = None,
        profile_mode: str = "all",  # "all", "reels", "photos"
        quality_preset: str = "best_video",
        max_items_per_profile: int = DEFAULT_MAX_ITEMS_PER_PROFILE,
        parent=None,
    ):
        super().__init__(parent)
        self.targets: List[str] = targets or []
        self.cookie_str: str = (cookie_str or "").strip()
        self.cookie_file: str = (cookie_file or "").strip()
        self.profile_mode: str = (profile_mode or "all").lower()
        self.quality_preset: str = quality_preset
        self.max_items_per_profile: int = max_items_per_profile
        self.is_cancelled: bool = False

        self._lock = threading.Lock()
        self.seen_ids: Set[str] = set()
        self._csrf_token: Optional[str] = self._extract_csrf_token()
        self._anon_cookies: Dict[str, str] = {}
        self._ssl_ctx = ssl._create_unverified_context()
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._profile_cache: Dict[str, Dict[str, Any]] = {}

        if not self.cookie_str and not self.cookie_file:
            try:
                from core.cookie_manager import CookieManager

                cm = CookieManager()
                c_str = cm.get_cookie_string()
                if c_str:
                    self.cookie_str = c_str
                    self._csrf_token = cm.get_csrf_token()
                fpath = cm.get_cookie_file_path()
                if fpath and os.path.exists(fpath):
                    self.cookie_file = fpath
            except Exception as e:
                logger.debug("Failed to auto-load cookies from CookieManager: %s", e)

        if self.cookie_str or self.cookie_file:
            logger.info("Session initialized: Authenticated cookies ACTIVE.")
        else:
            logger.warning("Session initialized: Running in UNAUTHENTICATED mode.")

    def _bootstrap_anonymous_session(self) -> None:
        """Handshakes with Instagram root to obtain mid, ig_did, datr, and csrftoken."""
        if self._anon_cookies or self.cookie_str or self.is_cancelled:
            return

        try:
            req = urllib.request.Request(
                IG_BASE_URL + "/",
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                },
            )
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=8) as resp:
                set_cookies = resp.headers.get_all("Set-Cookie") or []
                for header in set_cookies:
                    parts = header.split(";")
                    if parts:
                        cookie_pair = parts[0].strip()
                        if "=" in cookie_pair:
                            k, v = cookie_pair.split("=", 1)
                            self._anon_cookies[k.strip()] = v.strip()

                if "csrftoken" in self._anon_cookies and not self._csrf_token:
                    self._csrf_token = self._anon_cookies["csrftoken"]
        except Exception as exc:
            logger.debug("Anonymous session bootstrap failed: %s", exc)

    def cancel(self) -> None:
        """Gracefully flags cancellation and shuts down thread executor."""
        self.is_cancelled = True
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def _extract_csrf_token(self) -> Optional[str]:
        if not self.cookie_str:
            return None
        m = re.search(r"(?:^|;\s*|\b)csrftoken=([^;]+)", self.cookie_str)
        return m.group(1) if m else None

    def _ensure_cookie_file(self) -> Optional[str]:
        if self.cookie_file and os.path.exists(self.cookie_file):
            return self.cookie_file
        try:
            from core.cookie_manager import CookieManager

            cm = CookieManager()
            fpath = cm.get_cookie_file_path()
            if fpath and os.path.exists(fpath):
                self.cookie_file = fpath
                return fpath
            if self.cookie_str:
                cm._cookie_string = self.cookie_str
                if cm.save_to_netscape_file():
                    fpath = cm.get_cookie_file_path()
                    if fpath and os.path.exists(fpath):
                        self.cookie_file = fpath
                        return fpath
        except Exception as e:
            logger.debug(f"Failed to ensure cookie file: {e}")
        return None

    def _apply_gaussian_pacing(self) -> None:
        """Applies a human-like Gaussian randomized delay between page requests."""
        delay = random.gauss(PROFILE_PAGING_MEAN_DELAY, PROFILE_PAGING_STD_DEV)
        sleep_time = max(MIN_PAGING_DELAY, min(delay, MAX_PAGING_DELAY))

        start = time.time()
        while time.time() - start < sleep_time:
            if self.is_cancelled:
                break
            time.sleep(0.1)

    def _apply_macro_pacing(self, page_number: int) -> None:
        """Enforces a natural dwell pause every 4 pages (~48 items) to break sustained velocity."""
        if page_number > 0 and page_number % 4 == 0:
            rest_seconds = random.uniform(14.0, 22.0)
            logger.info(
                "Macro rest triggered at page %d. Resting for %.1fs...",
                page_number,
                rest_seconds,
            )
            start_t = time.time()
            while time.time() - start_t < rest_seconds:
                if self.is_cancelled:
                    break
                rem = max(0.0, rest_seconds - (time.time() - start_t))
                self.status_message.emit(
                    f"☕ Natural dwell rest: {rem:.1f}s remaining (page {page_number})..."
                )
                time.sleep(0.5)

    def _build_headers(
        self,
        referer: str = "https://www.instagram.com/",
        require_auth: bool = False,
        is_mobile: bool = False,
    ) -> Dict[str, str]:
        """Construct browser-like HTTP headers with strict surface boundary isolation."""
        if is_mobile:
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "Accept-Encoding": "gzip, deflate",
                "X-IG-App-ID": IG_APP_ID,
                "X-FB-HTTP-Engine": "Liger",
                "Connection": "keep-alive",
            }
            # Strictly DO NOT attach web cookies (sessionid, datr) to mobile headers
            return headers

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "X-IG-App-ID": IG_APP_ID,
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": IG_BASE_URL,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        # Attach Web session credentials exclusively to web surfaces
        if require_auth and self.cookie_str:
            headers["Cookie"] = self.cookie_str
            if self._csrf_token:
                headers["X-CSRFToken"] = self._csrf_token
        elif self._anon_cookies:
            headers["Cookie"] = "; ".join(
                f"{k}={v}" for k, v in self._anon_cookies.items()
            )
            csrf = self._anon_cookies.get("csrftoken") or self._csrf_token
            if csrf:
                headers["X-CSRFToken"] = csrf

        return headers

    def _is_safe_response(
        self, response_url: str, response_text: str, status_code: int
    ) -> bool:
        """
        Circuit-breaker: Detects checkpoint redirects, scraping warnings, and rate limits.
        Aborts immediately to protect the session from suspension.
        """
        warning_indicators = (
            "/accounts/scraping_warning/",
            "checkpoint_required",
            "challenge_required",
            "feedback_required",
        )

        final_url = response_url.lower()
        if any(ind in final_url for ind in warning_indicators):
            logger.error(
                "Scraping warning/checkpoint detected in URL. Aborting worker."
            )
            self.status_message.emit(
                "⚠️ Scraping warning detected by Instagram. Pausing to protect account."
            )
            self.cancel()
            return False

        if any(ind in response_text for ind in warning_indicators):
            logger.error(
                "Scraping warning/checkpoint detected in payload. Aborting worker."
            )
            self.status_message.emit(
                "⚠️ Scraping warning detected in payload. Pausing to protect account."
            )
            self.cancel()
            return False

        if status_code == 429:
            logger.warning(
                "HTTP 429 Too Many Requests detected. Activating circuit breaker."
            )
            self.status_message.emit(
                "⚠️ HTTP 429 (Too Many Requests). Pausing inspection to protect account."
            )
            self.cancel()
            return False

        if status_code in (401, 403):
            logger.warning(f"HTTP {status_code} Forbidden/Unauthorized received.")
            return False

        return True

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        caller_tag: str = "",
        require_auth: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Centralized HTTP request handler with decompression and cookie synchronizing."""
        if self.is_cancelled:
            return None

        if not self.cookie_str and not self._anon_cookies:
            self._bootstrap_anonymous_session()

        req_headers = self._build_headers(require_auth=require_auth)
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout
            ) as resp:
                status_code = getattr(resp, "status", 200)
                final_url = resp.geturl()

                set_cookies = resp.headers.get_all("Set-Cookie") or []
                for sc in set_cookies:
                    parts = sc.split(";")
                    if parts:
                        pair = parts[0].strip()
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            self._anon_cookies[k.strip()] = v.strip()
                            if k.strip() == "csrftoken" and not self.cookie_str:
                                self._csrf_token = v.strip()

                raw_bytes = resp.read()

                content_encoding = resp.headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or (
                    len(raw_bytes) >= 2 and raw_bytes[:2] == b"\x1f\x8b"
                ):
                    try:
                        raw_bytes = gzip.decompress(raw_bytes)
                    except Exception:
                        pass
                elif "deflate" in content_encoding:
                    try:
                        raw_bytes = zlib.decompress(raw_bytes)
                    except Exception:
                        try:
                            raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
                        except Exception:
                            pass

                charset = resp.headers.get_content_charset() or "utf-8"
                raw = raw_bytes.decode(charset, errors="replace").strip()
                raw = raw.lstrip("\ufeff").strip()

                if not self._is_safe_response(final_url, raw, status_code):
                    return None

                if raw.startswith(("{", "[")):
                    return json.loads(raw)
                return None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.status_message.emit(
                    "⚠️ [Rate Limit] HTTP 429: Too Many Requests. Pausing to protect account."
                )
                self.cancel()
            elif e.code in (400, 401, 403):
                logger.debug("[%s] HTTP %d: %s", caller_tag or "API", e.code, e.reason)
            return None
        except Exception as e:
            logger.debug("[%s] Request to %s failed: %s", caller_tag or "API", url, e)
            return None

    def _get_user_id(self, username: str) -> Optional[str]:
        """Resolves username to Instagram User ID using multi-strategy fallback."""
        username = username.lower().strip().lstrip("@")
        self.status_message.emit(f"🔍 [Resolver] Fetching User ID for @{username}...")

        # Strategy 1: Web Profile Info Endpoint
        try:
            url1 = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            res1 = self._make_request(
                url1,
                headers={
                    "Referer": f"{IG_BASE_URL}/{username}/",
                    "X-IG-App-ID": IG_APP_ID,
                },
                caller_tag="WebProfileInfo",
                require_auth=False,
            )
            if not res1 and self.cookie_str:
                res1 = self._make_request(
                    url1,
                    headers={
                        "Referer": f"{IG_BASE_URL}/{username}/",
                        "X-IG-App-ID": IG_APP_ID,
                    },
                    caller_tag="WebProfileInfoAuth",
                    require_auth=True,
                )

            if res1 and isinstance(res1, dict):
                user_data = res1.get("data", {}).get("user") or res1.get("user")
                if user_data:
                    self._profile_cache[username] = user_data
                    uid = user_data.get("id") or user_data.get("pk")
                    if uid:
                        uid_str = str(uid)
                        self.status_message.emit(
                            f"✓ [Resolver] Found User ID: {uid_str} (@{username})"
                        )
                        return uid_str
        except Exception as e:
            logger.debug(f"WebProfileInfo resolver failed: {e}")

        # Strategy 2: Base HTML Scraper Fallback
        try:
            profile_url = f"{IG_BASE_URL}/{username}/"
            req = urllib.request.Request(
                profile_url,
                headers=self._build_headers(referer=IG_BASE_URL, require_auth=False),
            )
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                raw_bytes = resp.read()
                content_encoding = resp.headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or (
                    len(raw_bytes) >= 2 and raw_bytes[:2] == b"\x1f\x8b"
                ):
                    raw_bytes = gzip.decompress(raw_bytes)
                elif "deflate" in content_encoding:
                    raw_bytes = zlib.decompress(raw_bytes)

                html_text = raw_bytes.decode("utf-8", errors="replace")
                patterns = [
                    r'"user_id":"(\d+)"',
                    r'"owner":\{"id":"(\d+)"',
                    r'"profile_id":"(\d+)"',
                    r'"user":\{"id":"(\d+)"',
                    r'"id":"(\d+)","username":"' + re.escape(username) + r'"',
                    r'"pk":"?(\d+)"?',
                ]
                for pat in patterns:
                    m = re.search(pat, html_text)
                    if m:
                        uid = m.group(1)
                        self.status_message.emit(
                            f"✓ [Resolver] Found User ID via HTML: {uid} (@{username})"
                        )
                        return uid
        except Exception as e:
            logger.debug(f"HTML scraper resolver failed: {e}")

        # Strategy 3: TopSearch Query Endpoint
        try:
            url3 = f"https://www.instagram.com/web/search/topsearch/?query={username}"
            res3 = self._make_request(
                url3,
                headers={"Referer": f"{IG_BASE_URL}/{username}/"},
                caller_tag="TopSearch",
                require_auth=False,
            )
            if res3 and isinstance(res3, dict) and "users" in res3:
                for item in res3["users"]:
                    u = item.get("user") or {}
                    if str(u.get("username", "")).lower() == username:
                        uid = u.get("pk") or u.get("id")
                        if uid:
                            self.status_message.emit(
                                f"✓ [Resolver] Found User ID: {uid} (@{username})"
                            )
                            return str(uid)
        except Exception:
            pass

        return None

    def _fetch_user_clips_mobile(
        self, username: str, user_id: str, max_items: int = 36
    ) -> int:
        """Tier 3: Dedicated Mobile Clips API with strict unauthenticated isolation."""
        url = "https://i.instagram.com/api/v1/clips/user/"
        headers = self._build_headers(is_mobile=True)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        has_next_page = True
        max_id: Optional[str] = None
        pages = 0
        found_count = 0
        anon_uuid = f"android-{''.join(random.choices('0123456789abcdef', k=16))}"

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if len(self.seen_ids) >= max_items:
                break

            post_params: Dict[str, Any] = {
                "_uuid": anon_uuid,
                "target_user_id": str(user_id),
                "page_size": "24",
                "include_feed_video": "true",
            }
            if max_id:
                post_params["max_id"] = str(max_id)

            encoded_payload = urllib.parse.urlencode(post_params).encode("utf-8")

            # Surface rule: Mobile endpoints are queried unauthenticated to protect user cookies
            res = self._make_request(
                url,
                headers=headers,
                data=encoded_payload,
                method="POST",
                caller_tag="MobileUserClips",
                require_auth=False,
            )

            if not isinstance(res, dict):
                break

            items = res.get("items")
            if not isinstance(items, list) or not items:
                break

            for item_container in items:
                if self.is_cancelled:
                    return found_count
                if not isinstance(item_container, dict):
                    continue

                media = (
                    item_container.get("media")
                    if isinstance(item_container.get("media"), dict)
                    else item_container
                )
                for card in self._extract_media_cards(
                    media, fallback_username=username
                ):
                    card["media_type"] = "REEL"
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                            found_count += 1

            has_next_page = bool(res.get("more_available", False))
            max_id = str(res.get("paging_token") or res.get("max_id") or "")
            if not max_id:
                has_next_page = False

            pages += 1
            self.status_message.emit(
                f"✓ [Mobile Clips] Page {pages}: {len(self.seen_ids)} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()

        return found_count

    def _fetch_user_feed_mobile(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginated Mobile Timeline Feed API with micro-jitter and macro-cooldowns."""
        headers = self._build_headers(is_mobile=True)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        has_next_page = True
        next_max_id: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                logger.info(
                    "Reached safe crawl batch limit (%d items) for @%s.",
                    self.max_items_per_profile,
                    username,
                )
                break

            feed_url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/"
            if next_max_id:
                feed_url += f"?max_id={urllib.parse.quote(str(next_max_id))}"

            res = self._make_request(
                feed_url,
                headers=headers,
                caller_tag="MobileUserFeed",
                require_auth=bool(self.cookie_str),
            )
            if not isinstance(res, dict):
                break

            items = res.get("items")
            if not isinstance(items, list) or not items:
                break

            for item in items:
                if self.is_cancelled:
                    return found_count
                if not isinstance(item, dict):
                    continue

                is_vid = (
                    is_standalone_video(item)
                    or bool(item.get("is_video"))
                    or item.get("media_type") == 2
                )
                if filter_mode == "reels" and not is_vid:
                    continue
                if filter_mode == "photos" and is_vid:
                    continue

                for card in self._extract_media_cards(item, fallback_username=username):
                    if is_vid and filter_mode == "reels":
                        card["media_type"] = "REEL"
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                            found_count += 1

            has_next_page = bool(res.get("more_available", False))
            next_max_id = str(res.get("next_max_id") or "")
            if not next_max_id:
                has_next_page = False

            pages += 1
            self.status_message.emit(
                f"✓ [Mobile Feed] Page {pages}: {len(self.seen_ids)} items found..."
            )

            if has_next_page and not self.is_cancelled:
                # 1. Micro pacing between standard pages
                self._apply_gaussian_pacing()
                # 2. Macro dwell rest every 4 pages (~48 items)
                self._apply_macro_pacing(pages)

        return found_count

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
        """Parses raw Instagram media dictionaries into normalized MediaCard schema."""
        if not item or not isinstance(item, dict):
            return []

        media = item.get("media") or item
        if not isinstance(media, dict):
            return []

        shortcode = str(media.get("code") or media.get("shortcode") or "")
        user_info = media.get("user") or media.get("owner")
        if not isinstance(user_info, dict):
            user_info = {}
        username = str(user_info.get("username") or fallback_username)

        # Safe Carousel Extraction
        sidecar_edges: list[dict[str, Any]] = []
        sidecar_obj = media.get("edge_sidecar_to_children")
        if isinstance(sidecar_obj, dict):
            raw_edges = sidecar_obj.get("edges")
            if isinstance(raw_edges, list):
                sidecar_edges = [
                    edge.get("node")
                    for edge in raw_edges
                    if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
                ]

        raw_carousel = media.get("carousel_media")
        carousel_children = (
            raw_carousel if isinstance(raw_carousel, list) else sidecar_edges
        )

        # Safe Caption Extraction
        caption_obj = media.get("caption")
        caption_text = ""
        if isinstance(caption_obj, dict):
            caption_text = str(caption_obj.get("text") or "")
        elif isinstance(caption_obj, str):
            caption_text = caption_obj
        elif "edge_media_to_caption" in media:
            edge_caption_obj = media.get("edge_media_to_caption")
            if isinstance(edge_caption_obj, dict):
                edges = edge_caption_obj.get("edges")
                if isinstance(edges, list) and edges and isinstance(edges[0], dict):
                    node = edges[0].get("node")
                    if isinstance(node, dict):
                        caption_text = str(node.get("text") or "")

        clean_caption = caption_text.strip()
        caption_lines = [
            line.strip() for line in clean_caption.splitlines() if line.strip()
        ]
        first_line = caption_lines[0] if caption_lines else ""

        # 1. Multi-Item Carousel Post -> Consolidate into ONE Card
        if carousel_children:
            total = len(carousel_children)
            slides: list[dict[str, Any]] = []
            for idx, child in enumerate(carousel_children, start=1):
                if not isinstance(child, dict):
                    continue
                child_id = str(child.get("id") or f"{shortcode}_{idx}")
                is_vid = bool(
                    child.get("is_video")
                    or child.get("media_type") == 2
                    or child.get("video_versions")
                    or child.get("__typename") == "GraphVideo"
                )

                v_url = ""
                if is_vid:
                    v_versions = child.get("video_versions")
                    v_url = (
                        str(v_versions[0].get("url") or "")
                        if isinstance(v_versions, list)
                        and v_versions
                        and isinstance(v_versions[0], dict)
                        else str(child.get("video_url") or "")
                    )

                display_url = str(child.get("display_url") or "")
                disp_res = child.get("display_resources")
                img_v2 = child.get("image_versions2")

                if isinstance(disp_res, list) and disp_res:
                    best_res = max(
                        disp_res,
                        key=lambda r: (
                            r.get("config_width", 0) if isinstance(r, dict) else 0
                        ),
                    )
                    full_img_url = (
                        str(best_res.get("src") or "")
                        if isinstance(best_res, dict)
                        else ""
                    ) or display_url
                elif (
                    isinstance(img_v2, dict)
                    and isinstance(img_v2.get("candidates"), list)
                    and img_v2["candidates"]
                ):
                    valid_c = [c for c in img_v2["candidates"] if isinstance(c, dict)]
                    if valid_c:
                        best_res = max(
                            valid_c,
                            key=lambda r: (r.get("width", 0) * r.get("height", 0)),
                        )
                        full_img_url = str(best_res.get("url") or "") or display_url
                    else:
                        full_img_url = display_url
                else:
                    full_img_url = display_url

                slides.append(
                    {
                        "index": idx,
                        "id": child_id,
                        "is_video": is_vid,
                        "video_url": v_url,
                        "download_url": v_url if is_vid else full_img_url,
                        "thumbnail_url": full_img_url,
                    }
                )

            primary_thumb = slides[0]["thumbnail_url"] if slides else ""
            title_line = (
                first_line
                if first_line
                else f"Instagram Carousel #{shortcode} ({total} items)"
            )

            card = {
                "id": str(media.get("id") or shortcode),
                "shortcode": shortcode,
                "title": title_line,
                "username": username,
                "url": raw_target or f"https://www.instagram.com/p/{shortcode}/",
                "thumbnail_url": primary_thumb,
                "video_url": "",
                "download_url": f"https://www.instagram.com/p/{shortcode}/",
                "caption": caption_text,
                "duration": 0.0,
                "view_count": int(
                    media.get("view_count") or media.get("play_count") or 0
                ),
                "like_count": int(media.get("like_count") or 0),
                "media_type": f"CAROUSEL ({total})",
                "carousel_count": total,
                "slides": slides,
                "quality": self.quality_preset,
                "selected": True,
                "status": "ready",
            }
            return [card]

        # 2. Single Post / Reel / Photo
        is_vid = (
            is_standalone_video(media)
            or bool(media.get("is_video"))
            or media.get("__typename") == "GraphVideo"
        )
        v_url = ""
        if is_vid:
            v_versions = media.get("video_versions")
            v_url = (
                str(v_versions[0].get("url") or "")
                if isinstance(v_versions, list)
                and v_versions
                and isinstance(v_versions[0], dict)
                else str(media.get("video_url") or "")
            )

        display_url = str(media.get("display_url") or "")
        disp_res = media.get("display_resources")
        img_v2 = media.get("image_versions2")

        if isinstance(disp_res, list) and disp_res:
            best_res = max(
                disp_res,
                key=lambda r: r.get("config_width", 0) if isinstance(r, dict) else 0,
            )
            full_img_url = (
                str(best_res.get("src") or "") if isinstance(best_res, dict) else ""
            ) or display_url
        elif (
            isinstance(img_v2, dict)
            and isinstance(img_v2.get("candidates"), list)
            and img_v2["candidates"]
        ):
            valid_c = [c for c in img_v2["candidates"] if isinstance(c, dict)]
            if valid_c:
                best_res = max(
                    valid_c,
                    key=lambda r: (r.get("width", 0) * r.get("height", 0)),
                )
                full_img_url = str(best_res.get("url") or "") or display_url
            else:
                full_img_url = display_url
        else:
            full_img_url = display_url

        if is_vid:
            b_type = (
                "REEL"
                if (
                    "/reel/" in raw_target.lower()
                    or "/reels/" in raw_target.lower()
                    or media.get("product_type") == "clips"
                    or self.profile_mode == "reels"
                )
                else "VIDEO"
            )
        else:
            b_type = "IMAGE"

        title_line = first_line if first_line else f"Instagram {b_type} #{shortcode}"
        canonical_url = (
            raw_target
            if raw_target
            else (
                f"https://www.instagram.com/reel/{shortcode}/"
                if b_type == "REEL"
                else f"https://www.instagram.com/p/{shortcode}/"
            )
        )

        card = {
            "id": str(media.get("id") or shortcode),
            "shortcode": shortcode,
            "title": title_line,
            "username": username,
            "url": canonical_url,
            "thumbnail_url": full_img_url,
            "video_url": v_url,
            "download_url": v_url if is_vid else full_img_url,
            "caption": caption_text,
            "duration": float(media.get("video_duration") or 0.0),
            "view_count": int(media.get("view_count") or media.get("play_count") or 0),
            "like_count": int(media.get("like_count") or 0),
            "media_type": b_type,
            "quality": self.quality_preset,
            "selected": True,
            "status": "ready",
        }
        return [card]

    def _inspect_single_post(
        self, shortcode: str, raw_target: str = "", media_type: str = "POST"
    ) -> List[Dict[str, Any]]:
        """Tier 1 -> Tier 2 -> Tier 3 inspection for single post URLs."""
        target_url = raw_target or f"https://www.instagram.com/p/{shortcode}/"

        # Tier 1: Instagram Mobile Media Info API (Unauthenticated First)
        media_id = shortcode_to_id(shortcode)
        if media_id:
            info_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
            res_mobile = self._make_request(
                info_url,
                caller_tag="MobileMediaInfo",
                require_auth=False,
            )
            # Retry with auth if unauthenticated call returned no items
            if not res_mobile and self.cookie_str:
                res_mobile = self._make_request(
                    info_url,
                    caller_tag="MobileMediaInfoAuth",
                    require_auth=True,
                )

            if res_mobile and isinstance(res_mobile, dict) and res_mobile.get("items"):
                extracted = self._extract_media_cards(
                    res_mobile["items"][0], raw_target=target_url
                )
                if extracted:
                    with self._lock:
                        for card in extracted:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                                self.media_found.emit(card)
                    return extracted

        # Tier 2: Instagram Web JSON Endpoint
        api_url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
        res_web = self._make_request(
            api_url,
            caller_tag="WebJSONPost",
            require_auth=False,
        )
        if isinstance(res_web, dict):
            graphql_data = res_web.get("graphql")
            data_dict = res_web.get("data")
            raw_items = res_web.get("items")

            media_data = (
                (
                    graphql_data.get("shortcode_media")
                    if isinstance(graphql_data, dict)
                    else None
                )
                or (
                    data_dict.get("xdt_shortcode_media")
                    if isinstance(data_dict, dict)
                    else None
                )
                or (
                    raw_items[0]
                    if isinstance(raw_items, list)
                    and raw_items
                    and isinstance(raw_items[0], dict)
                    else None
                )
            )
            if isinstance(media_data, dict):
                extracted = self._extract_media_cards(media_data, raw_target=target_url)
                if extracted:
                    with self._lock:
                        for card in extracted:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                                self.media_found.emit(card)
                    return extracted

        # Tier 3: Direct yt-dlp fallback
        self._inspect_via_ytdlp(target_url)
        return []

    def _fetch_timeline_graphql(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Tier 2/3: Paginate user timeline media via POST-based PolarisProfilePostsQuery."""
        DOC_ID_POSTS = "6915638531862590"
        FRIENDLY_NAME = "PolarisProfilePostsQuery"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if len(self.seen_ids) >= self.max_items_per_profile:
                logger.info(
                    "Reached safe crawl cap (%d items) for @%s.",
                    self.max_items_per_profile,
                    username,
                )
                break

            variables: Dict[str, Any] = {
                "after": end_cursor,
                "first": 24,
                "id": str(user_id),
            }

            payload = {
                "doc_id": DOC_ID_POSTS,
                "variables": json.dumps(variables, separators=(",", ":")),
            }
            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-IG-App-ID": IG_APP_ID,
                "X-FB-Friendly-Name": FRIENDLY_NAME,
                "Referer": f"{IG_BASE_URL}/{username}/",
                "Origin": IG_BASE_URL,
            }

            res = self._make_request(
                "https://www.instagram.com/graphql/query",
                headers=headers,
                data=encoded_data,
                method="POST",
                caller_tag="GraphQLTimeline",
                require_auth=bool(self.cookie_str),
            )
            if not isinstance(res, dict):
                break

            data_obj = res.get("data")
            if not isinstance(data_obj, dict):
                break

            user_data = (
                data_obj.get("xdt_api__v1__feed__timeline__connection_v2")
                or data_obj.get("user", {}).get("edge_owner_to_timeline_media")
                or data_obj.get("xdt_api__v1__feed__user_timeline_graphql_connection")
            )
            if not isinstance(user_data, dict):
                break

            timeline_media = (
                user_data.get("edge_owner_to_timeline_media")
                if isinstance(user_data.get("edge_owner_to_timeline_media"), dict)
                else user_data
            )

            edges = timeline_media.get("edges")
            if not isinstance(edges, list) or not edges:
                break

            for edge in edges:
                if self.is_cancelled:
                    return found_count
                if not isinstance(edge, dict):
                    continue

                node = edge.get("node") if isinstance(edge.get("node"), dict) else edge
                if not isinstance(node, dict):
                    continue

                is_vid = (
                    is_standalone_video(node)
                    or bool(node.get("is_video"))
                    or node.get("media_type") == 2
                    or node.get("product_type") == "clips"
                    or node.get("__typename") in ("GraphVideo", "GraphStoryVideo")
                )

                if filter_mode == "reels" and not is_vid:
                    continue
                if filter_mode == "photos" and is_vid:
                    continue

                for card in self._extract_media_cards(node, fallback_username=username):
                    if is_vid and filter_mode == "reels":
                        card["media_type"] = "REEL"
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                            found_count += 1

            page_info = timeline_media.get("page_info")
            if isinstance(page_info, dict):
                has_next_page = bool(page_info.get("has_next_page", False))
                end_cursor = page_info.get("end_cursor")
            else:
                has_next_page = False
                end_cursor = None

            pages += 1
            self.status_message.emit(
                f"✓ [GraphQL Timeline] Page {pages}: {len(self.seen_ids)} items found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Deep crawler cascading through Web GraphQL, Mobile Clips, and Mobile Timeline tiers."""
        tier_label = (
            "Reels"
            if filter_mode == "reels"
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Tier 1: Web Profile] Crawling {tier_label} for @{username}..."
        )

        # Tier 1: Dedicated GraphQL Reels Tab (Fastest if authenticated)
        if filter_mode in ("reels", "all") and not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 2: GraphQL Reels] Querying dedicated Reels connection for @{username}..."
            )
            self._fetch_reels_graphql(
                username, user_id, max_items=self.max_items_per_profile
            )

        # Tier 2: Timeline GraphQL Pagination
        if len(self.seen_ids) == 0 and not self.is_cancelled:
            self._fetch_timeline_graphql(username, user_id, filter_mode=filter_mode)

        # Tier 3: Mobile Dedicated Clips API (High-resiliency unauthenticated endpoint)
        if (
            len(self.seen_ids) == 0
            and filter_mode in ("reels", "all")
            and not self.is_cancelled
        ):
            self.status_message.emit(
                f"🚀 [Tier 3: Mobile Clips API] Fetching clips stream for @{username}..."
            )
            self._fetch_user_clips_mobile(
                username, user_id, max_items=self.max_items_per_profile
            )

        # Tier 3.5: Mobile App Timeline Gateway Fallback
        if len(self.seen_ids) == 0 and not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 3.5: Mobile Feed API] Fetching mobile feed for @{username}..."
            )
            self._fetch_user_feed_mobile(username, user_id, filter_mode=filter_mode)

        # Tier 4: Unauthenticated guidance notification
        if len(self.seen_ids) == 0 and not self.cookie_str:
            self.status_message.emit(
                "💡 [Notice] 0 items found. User Reels feeds are login-gated by Instagram. Click 'Import Cookie' to enable full feed crawling."
            )

    def _inspect_via_ytdlp(
        self, url: str, default_username: str = "", filter_mode: str = "all"
    ) -> None:
        """Tier 4: yt-dlp flat extractor fallback with redirect evasion."""
        if yt_dlp is None:
            msg = "yt-dlp engine is not installed or available in this Python environment."
            logger.warning(msg)
            self.status_message.emit(f"⚠️ {msg}")
            return

        try:
            cfile = self._ensure_cookie_file()
            has_cookies = bool(cfile and os.path.exists(cfile))

            # Strip /reels/ to base profile URL when unauthenticated to prevent HTTP 302 login redirects
            if default_username:
                if filter_mode == "reels" and has_cookies:
                    clean_url = f"{IG_BASE_URL}/{default_username}/reels/"
                else:
                    clean_url = f"{IG_BASE_URL}/{default_username}/"
            else:
                raw_clean = normalize_url(url) or url
                if not has_cookies and "/reels" in raw_clean.lower():
                    clean_url = re.sub(
                        r"/reels/?$", "/", raw_clean, flags=re.IGNORECASE
                    )
                else:
                    clean_url = raw_clean

            self.status_message.emit(
                f"⚙️ [Tier 4: Engine Fallback] Running yt-dlp extraction for {clean_url}..."
            )
            ydl_opts: Dict[str, Any] = {
                "extract_flat": "in_playlist",
                "noplaylist": False,
                "no_warnings": True,
                "ignoreerrors": True,
                "skip_download": True,
                "logger": YTDLPQuietLogger(),
                "socket_timeout": DEFAULT_REQUEST_TIMEOUT,
                "nocheckcertificate": True,
                "http_headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://www.instagram.com/",
                },
            }
            if has_cookies and cfile:
                ydl_opts["cookiefile"] = cfile

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                if not info or not isinstance(info, dict):
                    return

                entries = info.get("entries") or [info]
                entries = [e for e in entries if e and isinstance(e, dict)]
                if not entries:
                    return

                uploader = (
                    info.get("uploader")
                    or entries[0].get("uploader")
                    or default_username
                    or "instagram"
                )

                for idx, entry in enumerate(entries, start=1):
                    item_code = str(entry.get("id") or f"media_{idx}")
                    entry_url = str(entry.get("webpage_url") or entry.get("url") or "")

                    has_video = bool(
                        entry.get("video_ext")
                        or (entry.get("vcodec") and entry.get("vcodec") != "none")
                        or entry.get("ext") == "mp4"
                        or "/reel/" in entry_url.lower()
                        or filter_mode == "reels"
                    )

                    badge_type = "REEL" if has_video else "IMAGE"

                    if filter_mode == "reels" and not has_video:
                        continue
                    if filter_mode == "photos" and has_video:
                        continue

                    with self._lock:
                        if item_code in self.seen_ids:
                            continue
                        self.seen_ids.add(item_code)

                    card_url = (
                        entry_url
                        if entry_url.startswith("http")
                        else (
                            f"{IG_BASE_URL}/reel/{item_code}/"
                            if badge_type == "REEL"
                            else f"{IG_BASE_URL}/p/{item_code}/"
                        )
                    )

                    card = {
                        "id": item_code,
                        "shortcode": item_code,
                        "title": entry.get("title")
                        or f"Instagram {badge_type} #{item_code}",
                        "username": entry.get("uploader") or uploader,
                        "url": card_url,
                        "thumbnail_url": entry.get("thumbnail") or "",
                        "video_url": entry.get("url") if has_video else "",
                        "download_url": entry.get("url")
                        or entry.get("thumbnail")
                        or card_url,
                        "caption": entry.get("description") or "",
                        "duration": float(entry.get("duration") or 0.0),
                        "view_count": int(entry.get("view_count") or 0),
                        "like_count": int(entry.get("like_count") or 0),
                        "media_type": badge_type,
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    self.item_found.emit(card)
                    self.media_found.emit(card)
        except Exception as ex:
            logger.debug("yt-dlp fallback error: %s", ex)

    def _fetch_stories_web(self, username: str, user_id: str) -> None:
        """Fetches active user stories using authenticated endpoints."""
        found_any = False
        endpoints = [
            f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/story/",
        ]

        for ep in endpoints:
            if self.is_cancelled:
                return
            res = self._make_request(
                ep,
                caller_tag="StoriesAPI",
                require_auth=True,
            )
            if not res or not isinstance(res, dict):
                continue

            items = []
            if isinstance(res.get("reels"), dict):
                user_reel = res["reels"].get(str(user_id))
                if isinstance(user_reel, dict) and isinstance(
                    user_reel.get("items"), list
                ):
                    items = user_reel["items"]
            elif isinstance(res.get("reels_media"), list) and res["reels_media"]:
                first_media = res["reels_media"][0]
                if isinstance(first_media, dict) and isinstance(
                    first_media.get("items"), list
                ):
                    items = first_media["items"]
            elif isinstance(res.get("items"), list):
                items = res["items"]

            if items:
                for idx, item in enumerate(items, start=1):
                    cards = self._extract_media_cards(item, fallback_username=username)
                    with self._lock:
                        for card in cards:
                            cid = str(card["id"])
                            card["media_type"] = "STORY"
                            card["title"] = f"@{username} Story ({idx}/{len(items)})"
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                                self.media_found.emit(card)
                                found_any = True
                if found_any:
                    return

        if not found_any and not self.is_cancelled:
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/stories/{username}/", default_username=username
            )

    def _fetch_reels_graphql(
        self, username: str, user_id: str, max_items: int = 120
    ) -> int:
        """Dedicated GraphQL Reels crawler using PolarisProfileReelsTabRootQuery.
        Executes successfully when valid authenticated cookies are present.
        """
        DOC_ID_REELS_PRIMARY = "7423376721066795"
        FRIENDLY_NAME = "PolarisProfileReelsTabRootQuery"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                break

            # Meta Polaris GraphQL AST structure for user clips connection
            variables: Dict[str, Any] = {
                "data": {
                    "include_feed_video": True,
                    "page_size": 24,
                    "target_user_id": str(user_id),
                }
            }
            if end_cursor:
                variables["data"]["max_id"] = str(end_cursor)

            payload = {
                "doc_id": DOC_ID_REELS_PRIMARY,
                "variables": json.dumps(variables, separators=(",", ":")),
            }
            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-IG-App-ID": IG_APP_ID,
                "X-FB-Friendly-Name": FRIENDLY_NAME,
                "Referer": f"{IG_BASE_URL}/{username}/reels/",
                "Origin": IG_BASE_URL,
            }

            res = self._make_request(
                "https://www.instagram.com/graphql/query",
                headers=headers,
                data=encoded_data,
                method="POST",
                caller_tag="GraphQLReelsTab",
                require_auth=True,  # Attaches sessionid and X-CSRFToken
            )

            if not isinstance(res, dict):
                break

            data_obj = res.get("data")
            if not isinstance(data_obj, dict):
                break

            connection = data_obj.get("xdt_api__v1__clips__user__connection_v2")
            if not isinstance(connection, dict):
                break

            edges = connection.get("edges")
            if not isinstance(edges, list) or not edges:
                break

            for edge in edges:
                if self.is_cancelled:
                    return found_count
                if not isinstance(edge, dict):
                    continue

                node = edge.get("node") if isinstance(edge.get("node"), dict) else edge
                if not isinstance(node, dict):
                    continue

                media_item = (
                    node.get("media") if isinstance(node.get("media"), dict) else node
                )
                for card in self._extract_media_cards(
                    media_item, fallback_username=username
                ):
                    card["media_type"] = "REEL"
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                            found_count += 1

            page_info = connection.get("page_info")
            if isinstance(page_info, dict):
                has_next_page = bool(page_info.get("has_next_page", False))
                end_cursor = page_info.get("end_cursor")
            else:
                has_next_page = False
                end_cursor = None

            pages += 1
            self.status_message.emit(
                f"✓ [GraphQL Reels] Page {pages}: {len(self.seen_ids)} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()

        return found_count

    def _inspect_single_target(self, raw_target: str) -> None:
        """Inspects an individual target URL across chained tiers."""
        if self.is_cancelled:
            return

        target = parse_instagram_url(raw_target)
        ttype = target.get("type")
        username = target.get("username")
        shortcode = target.get("shortcode")

        if ttype in ("reel", "post", "carousel", "tv") and shortcode:
            self._inspect_single_post(shortcode, raw_target=raw_target)
        elif ttype == "story" and username:
            uid = self._get_user_id(username)
            if uid:
                self._fetch_stories_web(username, uid)
            else:
                self._inspect_via_ytdlp(raw_target, default_username=username)
        elif ttype in ("profile", "profile_reels") and username:
            effective_mode = "reels" if ttype == "profile_reels" else self.profile_mode
            uid = self._get_user_id(username)
            if uid:
                self._fetch_all_profile_media_web(
                    username, uid, filter_mode=effective_mode
                )
            else:
                self._inspect_via_ytdlp(
                    raw_target, default_username=username, filter_mode=effective_mode
                )

            # Fallback to yt-dlp flat extractor if 0 items were retrieved via API
            if len(self.seen_ids) == 0 and not self.is_cancelled:
                self._inspect_via_ytdlp(
                    raw_target, default_username=username, filter_mode=effective_mode
                )
        else:
            self._inspect_via_ytdlp(raw_target)

    def run(self) -> None:
        """Sequential rate-limited execution loop across input targets."""
        try:
            total = len(self.targets)
            if total == 0:
                self.finished.emit(0)
                self.inspection_finished.emit(0)
                return

            self.progress.emit(10)

            for idx, target in enumerate(self.targets):
                if self.is_cancelled:
                    break

                self.status_message.emit(f"Inspecting ({idx + 1}/{total}): {target}")
                self._inspect_single_target(target)

                pct = int(10 + ((idx + 1) / total) * 85)
                self.progress.emit(pct)

                # Inter-target cooldown pause to protect account between multiple links
                if idx < total - 1 and not self.is_cancelled:
                    cooldown = random.uniform(
                        INTER_TARGET_COOLDOWN_MIN, INTER_TARGET_COOLDOWN_MAX
                    )
                    start_t = time.time()
                    while time.time() - start_t < cooldown:
                        if self.is_cancelled:
                            break
                        rem = max(0.0, cooldown - (time.time() - start_t))
                        self.status_message.emit(
                            f"⏳ Cooldown {rem:.1f}s before next target..."
                        )
                        time.sleep(0.5)

            self.progress.emit(100)
            with self._lock:
                total_found = len(self.seen_ids)
                if total_found <= 4 and not self.cookie_str:
                    self.status_message.emit(
                        f"Done: {total_found} items found. (Tip: Import cookies to crawl beyond Instagram's 4-item public limit)."
                    )
                self.finished.emit(total_found)
                self.inspection_finished.emit(total_found)
        except Exception as e:
            self.error.emit(f"Inspection error: {str(e)}")
            self.error_occurred.emit(f"Inspection error: {str(e)}")
            with self._lock:
                self.finished.emit(len(self.seen_ids))
                self.inspection_finished.emit(len(self.seen_ids))
