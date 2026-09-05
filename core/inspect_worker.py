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
import math
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
        IG_API_BASE_URL,
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
    IG_API_BASE_URL = "https://i.instagram.com/api/v1"
    IG_APP_ID = "936619743392459"
    IG_WEB_PROFILE_INFO_URL = (
        "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    )
    IG_FEED_USER_URL = "https://www.instagram.com/api/v1/feed/user/{user_id}/"
    IG_CLIPS_USER_URL = "https://www.instagram.com/api/v1/clips/user/"
    IG_USER_INFO_MOBILE_URL = "https://i.instagram.com/api/v1/users/{user_id}/info/"
    MAX_PAGINATION_PAGES = 15

# --- Anti-Scraping Protection & Adaptive Pacing Defaults ---
DEFAULT_MAX_ITEMS_PER_PROFILE = 36  # Safe default threshold (~3 grid pages)

# 1. Profile Crawl Pacing (Deep cursor-based pagination stream)
PROFILE_PAGING_MEAN_DELAY = 2.85
PROFILE_PAGING_STD_DEV = 0.25
MIN_PROFILE_PAGING_DELAY = 2.5
MAX_PROFILE_PAGING_DELAY = 3.2
PROFILE_MACRO_DWELL_INTERVAL = 4  # pages (~48 items)
PROFILE_MACRO_DWELL_MIN = 15.0
PROFILE_MACRO_DWELL_MAX = 22.0

# 2. Direct Media Inspection Pacing (Point-lookup single URLs)
DIRECT_INSPECT_MEAN_DELAY = 0.85
DIRECT_INSPECT_STD_DEV = 0.15
MIN_DIRECT_INSPECT_DELAY = 0.60
MAX_DIRECT_INSPECT_DELAY = 1.25
DIRECT_MACRO_DWELL_INTERVAL = 36  # items before brief micro-rest
DIRECT_MACRO_DWELL_MIN = 3.0
DIRECT_MACRO_DWELL_MAX = 5.0

# 3. Inter-Profile Cooldown (Between full profile scrapes)
INTER_PROFILE_COOLDOWN_MIN = 10.0
INTER_PROFILE_COOLDOWN_MAX = 18.0

from core.client_engine import ResilientSession
from core.parser import (
    NormalizedMedia,
    UnifiedInstagramParser,
    is_standalone_video,
    normalize_url,
    parse_instagram_url,
    shortcode_to_id,
)

# Instagram Web Client GraphQL Persisted Query Document IDs & Friendly Names
DOC_ID_USER_CLIPS = "8677440618991207"
FRIENDLY_NAME_CLIPS = "PolarisClipsTimelineProfileQuery"

DOC_ID_TIMELINE = "7095914977196024"
FRIENDLY_NAME_TIMELINE = "PolarisProfilePostsTimelineQuery"

DOC_ID_PROFILE_INFO = "6047242945377598"
FRIENDLY_NAME_PROFILE_INFO = "PolarisProfilePageHeaderQuery"

logger = logging.getLogger("InspectWorker")


def get_cookie_opener(
    cookie_path: Optional[str] = None,
) -> urllib.request.OpenerDirector:
    """
    Constructs an isolated OpenerDirector configured with certificate verification
    and Netscape cookie jar session handling at module scope.
    """
    handlers: List[urllib.request.BaseHandler] = []
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    handlers.append(urllib.request.HTTPSHandler(context=ctx))

    if cookie_path and os.path.exists(cookie_path):
        import http.cookiejar

        cj = http.cookiejar.MozillaCookieJar(cookie_path)
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
            handlers.append(urllib.request.HTTPCookieProcessor(cj))
        except Exception as exc:
            logger.debug(
                "Failed to load MozillaCookieJar from %s: %s", cookie_path, exc
            )

    return urllib.request.build_opener(*handlers)


class InstagramReelsResolver:
    """Handles multi-tier Reels querying with persisted doc_id failover
    and public timeline fallback.
    """

    def __init__(self, session: ResilientSession) -> None:
        self.session = session

    def fetch_user_reels(
        self,
        target_user_id: str | int,
        target_username: str,
        max_items: int = 24,
        cursor: str | None = None,
    ) -> list[NormalizedMedia]:
        results: list[NormalizedMedia] = []

        try:
            numeric_uid = int(str(target_user_id).strip())
        except (ValueError, TypeError):
            numeric_uid = 0

        # Tier 2: Dedicated GraphQL Clips Connection (PolarisClipsTimelineProfileQuery)
        variables_clips: dict[str, Any] = {
            "data": {
                "include_feed_video": True,
                "page_size": max_items,
                "target_user_id": numeric_uid,
            },
            "after": cursor if cursor else None,
            "before": None,
            "first": max_items,
            "last": None,
        }

        try:
            logger.debug(
                "[InspectWorker] Executing %s for user ID %d",
                FRIENDLY_NAME_CLIPS,
                numeric_uid,
            )
            data = self.session.execute_persisted_query(
                doc_id=DOC_ID_USER_CLIPS,
                variables=variables_clips,
                friendly_name=FRIENDLY_NAME_CLIPS,
            )

            data_root = data.get("data", {}) if isinstance(data, dict) else {}
            clips_connection = (
                data_root.get("xdt_api__v1__clips__user__connection_v2")
                or data_root.get("xdt_api__v1__clips__user__connection")
                or {}
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
                "[InspectWorker] [GraphQLReelsTab] Dedicated clips query failed (%s). Falling back to Timeline.",
                exc,
            )

        # Tier 2.1 Fallback: Query Profile Posts Timeline and Filter for Videos
        variables_timeline: dict[str, Any] = {
            "after": cursor if cursor else None,
            "first": max_items * 2,
            "id": str(target_user_id),
        }

        try:
            logger.debug(
                "[InspectWorker] Executing %s fallback for user %s",
                FRIENDLY_NAME_TIMELINE,
                target_user_id,
            )
            timeline_data = self.session.execute_persisted_query(
                doc_id=DOC_ID_TIMELINE,
                variables=variables_timeline,
                friendly_name=FRIENDLY_NAME_TIMELINE,
            )

            data_root = (
                timeline_data.get("data", {}) if isinstance(timeline_data, dict) else {}
            )
            user_node = (
                data_root.get("xdt_api__v1__feed__timeline__connection_v2")
                or data_root.get("user", {}).get("edge_owner_to_timeline_media")
                or data_root.get("xdt_api__v1__feed__user_timeline_graphql_connection")
            )

            edges = user_node.get("edges") if isinstance(user_node, dict) else None
            if isinstance(edges, list):
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    node = (
                        edge.get("node") if isinstance(edge.get("node"), dict) else edge
                    )
                    if not isinstance(node, dict):
                        continue

                    is_vid = bool(
                        node.get("is_video")
                        or node.get("media_type") == 2
                        or node.get("product_type") == "clips"
                    )
                    if is_vid:
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
        profile_mode: str = "all",
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
        self._current_target_index: int = 0
        self._current_sub_index: int = 0
        self._csrf_token: Optional[str] = self._extract_csrf_token()
        self._anon_cookies: Dict[str, str] = {}
        try:
            import certifi

            self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self._ssl_ctx = ssl.create_default_context()
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

        # Parse cookie map for ResilientSession (curl_cffi Chrome impersonation)
        cookie_dict: Dict[str, str] = {}
        if self.cookie_str:
            for pair in self.cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    cookie_dict[k.strip()] = v.strip()

        self.resilient_session = ResilientSession(cookies=cookie_dict)

        if self.cookie_str or self.cookie_file:
            logger.info("Session initialized: Authenticated cookies ACTIVE.")
        else:
            logger.warning("Session initialized: Running in UNAUTHENTICATED mode.")

    def _get_max_pages_ceiling(self, page_size: int = 24) -> int:
        """
        Computes the dynamic maximum pagination limit based on target item count.
        Returns a large upper bound (1000 pages) when crawling in unlimited mode (0).
        """
        if self.max_items_per_profile <= 0:
            return 1000  # Unlimited mode (~24,000 items)

        # Calculate required pages with a 2-page safety buffer for tombstoned/filtered items
        needed_pages = math.ceil(self.max_items_per_profile / page_size) + 2
        return max(MAX_PAGINATION_PAGES, needed_pages)

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

    def _extract_from_embed_html(
        self, html_text: str, shortcode: str, raw_target: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Extracts media metadata from Instagram captioned embed HTML documents.
        Resolves window.__additionalDataLoaded payloads, embedded JSON blobs,
        and applies carousel slide index extraction (?img_index=N).
        """
        if not html_text:
            return None

        media_data: Optional[Dict[str, Any]] = None

        # 1. Extract payload from window.__additionalDataLoaded('/p/...', {...})
        match_add_data = re.search(
            r"window\.__additionalDataLoaded\([^,]+,\s*(\{.+?\})\s*\);",
            html_text,
            re.DOTALL,
        )
        if match_add_data:
            try:
                payload = json.loads(match_add_data.group(1))
                media_data = (
                    payload.get("graphql", {}).get("shortcode_media")
                    or payload.get("data", {}).get("xdt_shortcode_media")
                    or payload.get("shortcode_media")
                )
            except Exception as exc:
                logger.debug("Failed to decode __additionalDataLoaded payload: %s", exc)

        # 2. Fallback: Search for JSON config embedded in application/json script tags
        if not media_data:
            match_script = re.search(
                r'<script\s+type="application/json"[^>]*>(\{.*?"shortcode_media".*?\})</script>',
                html_text,
                re.DOTALL,
            )
            if match_script:
                try:
                    cfg = json.loads(match_script.group(1))
                    media_data = cfg.get("graphql", {}).get("shortcode_media")
                except Exception:
                    pass

        if media_data and isinstance(media_data, dict):
            cards = self._extract_media_cards(media_data, raw_target=raw_target)
            if cards:
                card = dict(cards[0])
                # Handle ?img_index=N parameter in raw_target for multi-slide posts
                if "img_index=" in raw_target:
                    m_idx = re.search(r"img_index=(\d+)", raw_target)
                    if m_idx:
                        idx = int(m_idx.group(1))
                        slides = card.get("slides", [])
                        if isinstance(slides, list) and 1 <= idx <= len(slides):
                            selected_slide = slides[idx - 1]
                            card["thumbnail_url"] = selected_slide.get(
                                "thumbnail_url"
                            ) or card.get("thumbnail_url", "")
                            card["download_url"] = selected_slide.get(
                                "download_url"
                            ) or card.get("download_url", "")
                            if selected_slide.get("is_video"):
                                card["video_url"] = selected_slide.get("video_url", "")
                                card["media_type"] = "REEL"
                            else:
                                card["media_type"] = "IMAGE"
                            card["title"] = f"{card.get('title', '')} (Slide {idx})"
                return card

        return None

    def _sleep_interruptible(
        self, duration: float, status_msg: Optional[str] = None
    ) -> None:
        """Sleeps in small increments allowing responsive thread cancellation."""
        start_t = time.time()
        while time.time() - start_t < duration:
            if self.is_cancelled:
                break
            if status_msg:
                rem = max(0.0, duration - (time.time() - start_t))
                self.status_message.emit(f"{status_msg} ({rem:.1f}s remaining)...")
            time.sleep(0.1)

    def _apply_gaussian_pacing(self) -> None:
        """Applies a human-like Gaussian randomized delay between profile page requests."""
        if self.is_cancelled:
            return
        delay = random.gauss(PROFILE_PAGING_MEAN_DELAY, PROFILE_PAGING_STD_DEV)
        sleep_time = max(MIN_PROFILE_PAGING_DELAY, min(delay, MAX_PROFILE_PAGING_DELAY))
        self._sleep_interruptible(sleep_time)

    def _apply_macro_pacing(self, page_number: int) -> None:
        """
        Enforces two-tiered natural dwell pauses:
        1. Standard dwell every 4 pages (15-22s).
        2. Deep rate-limiter bucket drain every 12 pages (40-60s) for batch sizes >240.
        """
        if self.is_cancelled or page_number <= 0:
            return

        # Tier-2 Deep Rest for extended crawl depth
        if page_number % 12 == 0:
            deep_rest = random.uniform(40.0, 60.0)
            logger.info(
                "Deep session cooldown at page %d. Draining velocity bucket for %.1fs...",
                page_number,
                deep_rest,
            )
            self._sleep_interruptible(
                deep_rest,
                status_msg=f"🛡️ Deep crawl velocity cooldown (page {page_number})",
            )
            return

        # Tier-1 Standard Macro Rest
        if page_number % PROFILE_MACRO_DWELL_INTERVAL == 0:
            rest_seconds = random.uniform(
                PROFILE_MACRO_DWELL_MIN, PROFILE_MACRO_DWELL_MAX
            )
            logger.info(
                "Macro rest triggered at page %d. Resting for %.1fs...",
                page_number,
                rest_seconds,
            )
            self._sleep_interruptible(
                rest_seconds,
                status_msg=f"☕ Natural dwell rest (page {page_number})",
            )

    def _apply_direct_item_pacing(self, item_index: int) -> None:
        """Fast, calibrated micro-jitter for direct URL lookups with batch rest intervals."""
        if self.is_cancelled:
            return
        if item_index > 0 and item_index % DIRECT_MACRO_DWELL_INTERVAL == 0:
            rest_duration = random.uniform(
                DIRECT_MACRO_DWELL_MIN, DIRECT_MACRO_DWELL_MAX
            )
            self._sleep_interruptible(
                rest_duration,
                status_msg=f"⏳ Direct lookup micro-rest (item {item_index})",
            )
            return

        delay = random.gauss(DIRECT_INSPECT_MEAN_DELAY, DIRECT_INSPECT_STD_DEV)
        sleep_time = max(MIN_DIRECT_INSPECT_DELAY, min(delay, MAX_DIRECT_INSPECT_DELAY))
        self._sleep_interruptible(sleep_time)

    def _build_headers(
        self,
        referer: str = "https://www.instagram.com/",
        require_auth: bool = False,
        is_mobile: bool = False,
    ) -> Dict[str, str]:
        """Construct browser-like or mobile HTTP headers with authenticated session propagation."""
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
            if require_auth and self.cookie_str:
                headers["Cookie"] = self.cookie_str
                if self._csrf_token:
                    headers["X-CSRFToken"] = self._csrf_token
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
        """Circuit-breaker: Detects checkpoint redirects, spam tripwires, and action blocks."""
        critical_indicators = (
            "/accounts/scraping_warning/",
            "checkpoint_required",
            "challenge_required",
            "feedback_required",
            "consent_required",
            '"is_spam":true',
            '"is_spam": true',
        )

        final_url = response_url.lower()
        if any(ind in final_url for ind in critical_indicators):
            logger.error("Scraping warning/checkpoint detected in URL: %s", final_url)
            self.status_message.emit(
                "🛑 Safety checkpoint triggered. Inspection paused to safeguard account."
            )
            self.cancel()
            return False

        if any(ind in response_text for ind in critical_indicators):
            logger.error(
                "Action block / challenge detected in payload (feedback_required). Halting worker immediately."
            )
            self.status_message.emit(
                "🛑 Action block triggered (feedback_required). Inspection halted to protect your account."
            )
            self.cancel()
            return False

        if status_code == 429:
            logger.warning(
                "HTTP 429 Too Many Requests detected. Tripping circuit breaker."
            )
            self.status_message.emit(
                "⚠️ HTTP 429 (Too Many Requests). Halting inspection to protect account."
            )
            self.cancel()
            return False

        if "/accounts/login/" in final_url:
            logger.debug(
                "Endpoint redirected to login (unauthenticated or restricted target)."
            )
            return False

        if status_code in (401, 403):
            logger.debug("HTTP %d Forbidden/Unauthorized received.", status_code)
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
        fatal_429: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Centralized HTTP request handler with fault isolation and error payload inspection."""
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
                    try:
                        parsed_json = json.loads(raw)
                        return (
                            parsed_json
                            if isinstance(parsed_json, (dict, list))
                            else None
                        )
                    except json.JSONDecodeError as jde:
                        logger.debug(
                            "[%s] JSON parse failed: %s", caller_tag or "API", jde
                        )
                        return None
                return None

        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                raw_err = e.read()
                content_encoding = e.headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or (
                    len(raw_err) >= 2 and raw_err[:2] == b"\x1f\x8b"
                ):
                    raw_err = gzip.decompress(raw_err)
                elif "deflate" in content_encoding:
                    try:
                        raw_err = zlib.decompress(raw_err)
                    except Exception:
                        raw_err = zlib.decompress(raw_err, -zlib.MAX_WBITS)
                charset = e.headers.get_content_charset() or "utf-8"
                err_body = raw_err.decode(charset, errors="replace").strip()
            except Exception:
                pass

            # Inspect error payload for action blocks before handling error code
            if not self._is_safe_response(e.url or url, err_body, e.code):
                return None

            if e.code == 429:
                if fatal_429:
                    self.status_message.emit(
                        "⚠️ [Rate Limit] HTTP 429: Too Many Requests. Halting to protect account."
                    )
                    self.cancel()
                else:
                    logger.debug(
                        "[%s] Non-fatal HTTP 429 received; deferring to alternative resolvers.",
                        caller_tag or "API",
                    )
            else:
                logger.debug(
                    "[%s] HTTP %d: %s | Response: %s",
                    caller_tag or "API",
                    e.code,
                    e.reason,
                    err_body[:300],
                )
            return None

        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as net_err:
            logger.debug(
                "[%s] Network transport failure for %s: %s",
                caller_tag or "API",
                url,
                net_err,
            )
            return None

    def _resolve_canonical_url(self, target_url: str) -> str:
        """Resolves share tokens, HTTP 301/302 redirects, and HTML canonical tags to a canonical URL.

        Uses standard browser navigation headers (document request) to prevent edge routers
        from treating the probe as an internal XMLHttpRequest.
        """
        if not target_url or self.is_cancelled:
            return target_url

        self.status_message.emit("🔗 Resolving share token / canonical redirect...")

        # Document navigation headers (must NOT contain X-Requested-With or X-IG-App-ID)
        nav_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        if self.cookie_str:
            nav_headers["Cookie"] = self.cookie_str

        opener = get_cookie_opener(self.cookie_file)
        req = urllib.request.Request(target_url, headers=nav_headers, method="GET")

        try:
            with opener.open(req, timeout=10) as resp:
                final_url = resp.geturl()

                # Case 1: Standard HTTP 301/302 location change
                if final_url != target_url and not any(
                    x in final_url for x in ("/accounts/login/", "/accounts/")
                ):
                    parsed_final = parse_instagram_url(final_url)
                    code = parsed_final.get("shortcode")
                    if parsed_final.get("valid") and code and len(code) <= 13:
                        logger.info(
                            "Resolved redirect [%s] -> [%s]", target_url, final_url
                        )
                        return final_url

                # Case 2: Unauthenticated redirect to login wall with preserved ?next= destination
                if "/accounts/login/" in final_url:
                    parsed_login = urllib.parse.urlparse(final_url)
                    next_params = urllib.parse.parse_qs(parsed_login.query).get(
                        "next", []
                    )
                    if next_params:
                        next_path = urllib.parse.unquote(next_params[0])
                        candidate = urllib.parse.urljoin(IG_BASE_URL, next_path)
                        parsed_candidate = parse_instagram_url(candidate)
                        c_code = parsed_candidate.get("shortcode")
                        if (
                            parsed_candidate.get("valid")
                            and c_code
                            and len(c_code) <= 13
                        ):
                            logger.info(
                                "Extracted canonical URL from login next query: %s",
                                candidate,
                            )
                            return candidate

                # Case 3: Read HTML <head> for og:url or link canonical metadata
                raw_bytes = resp.read(65536)
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
                        pass

                html_head = raw_bytes.decode("utf-8", errors="replace")

                og_match = re.search(
                    r'<meta\s+property=["\']og:url["\']\s+content=["\'](https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_\-]+)/?)[^"\']*["\']',
                    html_head,
                    re.IGNORECASE,
                )
                if og_match and len(og_match.group(2)) <= 13:
                    canonical = og_match.group(1).rstrip("/") + "/"
                    logger.info(
                        "Discovered canonical URL via og:url meta tag: %s", canonical
                    )
                    return canonical

                canonical_match = re.search(
                    r'<link\s+rel=["\']canonical["\']\s+href=["\'](https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_\-]+)/?)[^"\']*["\']',
                    html_head,
                    re.IGNORECASE,
                )
                if canonical_match and len(canonical_match.group(2)) <= 13:
                    canonical = canonical_match.group(1).rstrip("/") + "/"
                    logger.info(
                        "Discovered canonical URL via link canonical tag: %s", canonical
                    )
                    return canonical

        except Exception as exc:
            logger.debug(
                "Failed network redirect resolution for %s: %s", target_url, exc
            )

        # Case 4: Deterministic 11-character shortcode slice fallback for 39-character tracking tokens
        parsed_target = parse_instagram_url(target_url)
        raw_code = parsed_target.get("shortcode")
        if raw_code and len(raw_code) > 13:
            candidate_code = raw_code[:11]
            if shortcode_to_id(candidate_code) is not None:
                media_path = "reel" if parsed_target.get("type") == "reel" else "p"
                reconstructed = f"{IG_BASE_URL}/{media_path}/{candidate_code}/"
                logger.info(
                    "Extracted 11-char canonical shortcode slice: [%s] -> [%s]",
                    raw_code,
                    candidate_code,
                )
                return reconstructed

        return target_url

    def _get_user_id(self, username: str) -> Optional[str]:
        """Resolves username to Instagram User ID and populates profile cache."""
        username = username.lower().strip().lstrip("@")
        self.status_message.emit(f"🔍 [Resolver] Fetching User ID for @{username}...")

        # Strategy 1: Web Profile Info Endpoint (Primary: Populates initial media cache + UID)
        try:
            url_info = (
                f"{IG_BASE_URL}/api/v1/users/web_profile_info/?username={username}"
            )
            res_info = self._make_request(
                url_info,
                headers={
                    "Referer": f"{IG_BASE_URL}/{username}/",
                    "X-IG-App-ID": IG_APP_ID,
                },
                caller_tag="WebProfileInfo",
                require_auth=bool(self.cookie_str),
                fatal_429=False,
            )
            if res_info and isinstance(res_info, dict):
                user_data = res_info.get("data", {}).get("user") or res_info.get("user")
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
            logger.debug("WebProfileInfo resolver failed: %s", e)

        # Strategy 2: Base HTML Scraper (Fallback for UID only)
        try:
            profile_url = f"{IG_BASE_URL}/{username}/"
            req = urllib.request.Request(
                profile_url,
                headers=self._build_headers(
                    referer=IG_BASE_URL, require_auth=bool(self.cookie_str)
                ),
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
            logger.debug("HTML scraper resolver failed: %s", e)

        # Strategy 3: TopSearch Query Endpoint
        try:
            url3 = f"{IG_BASE_URL}/web/search/topsearch/?query={username}"
            res3 = self._make_request(
                url3,
                headers={"Referer": f"{IG_BASE_URL}/{username}/"},
                caller_tag="TopSearch",
                require_auth=bool(self.cookie_str),
                fatal_429=False,
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
        except Exception as e:
            logger.debug("TopSearch resolver failed: %s", e)

        return None

    def _fetch_user_clips_graphql(
        self, username: str, user_id: str, max_items: int = 24
    ) -> int:
        """Paginates user Reels archive using PolarisClipsTimelineProfileQuery via ResilientSession."""
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=max_items)

        try:
            numeric_uid = int(str(user_id).strip())
        except (ValueError, TypeError):
            numeric_uid = 0

        # Ensure ResilientSession is instantiated with active session cookies
        if not hasattr(self, "resilient_session") or self.resilient_session is None:
            cookie_dict: Dict[str, str] = {}
            if self.cookie_str:
                for pair in self.cookie_str.split(";"):
                    if "=" in pair:
                        k, v = pair.strip().split("=", 1)
                        cookie_dict[k.strip()] = v.strip()
            self.resilient_session = ResilientSession(cookies=cookie_dict)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                break

            variables: Dict[str, Any] = {
                "data": {
                    "include_feed_video": True,
                    "page_size": max_items,
                    "target_user_id": numeric_uid,
                },
                "after": end_cursor if end_cursor else None,
                "before": None,
                "first": max_items,
                "last": None,
            }

            try:
                res = self.resilient_session.execute_persisted_query(
                    doc_id=DOC_ID_USER_CLIPS,
                    variables=variables,
                    friendly_name=FRIENDLY_NAME_CLIPS,
                )
            except PermissionError as pe:
                self.status_message.emit(f"🛑 [Security Alert] {pe}")
                self.cancel()
                break
            except Exception as exc:
                logger.debug("[GraphQLClips] Persisted query fault: %s", exc)
                break

            if not isinstance(res, dict):
                break

            data_root = res.get("data", {}) if isinstance(res, dict) else {}
            clips_conn = (
                data_root.get("xdt_api__v1__clips__user__connection_v2")
                or data_root.get("xdt_api__v1__clips__user__connection")
                or {}
            )
            edges = clips_conn.get("edges") if isinstance(clips_conn, dict) else None

            if not isinstance(edges, list) or not edges:
                break

            for edge in edges:
                if self.is_cancelled:
                    return found_count
                if not isinstance(edge, dict):
                    continue

                node = edge.get("node") if isinstance(edge.get("node"), dict) else edge
                media = (
                    node.get("media") if isinstance(node.get("media"), dict) else node
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

            page_info = (
                clips_conn.get("page_info") if isinstance(clips_conn, dict) else {}
            )
            if isinstance(page_info, dict):
                has_next_page = bool(page_info.get("has_next_page", False))
                end_cursor = page_info.get("end_cursor")
            else:
                has_next_page = False

            pages += 1
            self.status_message.emit(
                f"✓ [GraphQL Clips] Page {pages}: {len(self.seen_ids)} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_user_clips_mobile(
        self, username: str, user_id: str, max_items: int = 72
    ) -> int:
        """Mobile private API fallback routing to i.instagram.com."""
        url = f"{IG_API_BASE_URL}/clips/user/"
        headers = self._build_headers(
            referer=f"{IG_BASE_URL}/{username}/reels/",
            require_auth=bool(self.cookie_str),
            is_mobile=True,
        )
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        has_next_page = True
        max_id: Optional[str] = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=24)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                break

            post_params: Dict[str, Any] = {
                "target_user_id": str(user_id),
                "page_size": "24",
                "include_feed_video": "true",
            }
            if max_id:
                post_params["max_id"] = str(max_id)

            encoded_payload = urllib.parse.urlencode(post_params).encode("utf-8")

            res = self._make_request(
                url,
                headers=headers,
                data=encoded_payload,
                method="POST",
                caller_tag="MobileUserClips",
                require_auth=bool(self.cookie_str),
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
                f"✓ [Mobile Clips API] Page {pages}: {len(self.seen_ids)} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_user_feed_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user profile posts and reels using native web endpoints and session cookies."""
        headers = self._build_headers(
            referer=f"{IG_BASE_URL}/{username}/",
            require_auth=bool(self.cookie_str),
            is_mobile=False,
        )

        has_next_page = True
        next_max_id: Optional[str] = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=12)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                logger.info(
                    "Reached crawl target limit (%d items) for @%s.",
                    self.max_items_per_profile,
                    username,
                )
                break

            feed_url = f"{IG_BASE_URL}/api/v1/feed/user/{user_id}/"
            if next_max_id:
                feed_url += f"?max_id={urllib.parse.quote(str(next_max_id))}"

            res = self._make_request(
                feed_url,
                headers=headers,
                caller_tag="WebUserFeed",
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
                    or item.get("product_type") == "clips"
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

            # Check pagination flags
            has_next_page = bool(
                res.get("more_available", False)
                or res.get("auto_load_more_enabled", False)
            )
            raw_cursor = res.get("next_max_id") or res.get("max_id")
            next_max_id = str(raw_cursor).strip() if raw_cursor else None

            if not next_max_id:
                has_next_page = False

            pages += 1
            self.status_message.emit(
                f"✓ [Profile Feed] Page {pages}: {len(self.seen_ids)} items found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
        """Parses raw Instagram media dictionaries into normalized MediaCard schema with queue tracking."""
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

        self._current_sub_index += 1
        sub_idx = self._current_sub_index
        t_idx = getattr(self, "_current_target_index", 0)

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
                "is_video": any(s["is_video"] for s in slides),
                "quality": self.quality_preset,
                "selected": True,
                "status": "ready",
                "target_index": t_idx,
                "sub_index": sub_idx,
            }
            return [card]

        # 2. Single Post / Reel / Photo
        is_vid = bool(
            is_standalone_video(media)
            or media.get("is_video")
            or media.get("media_type") == 2
            or bool(media.get("video_versions"))
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
            "is_video": is_vid,
            "quality": self.quality_preset,
            "selected": True,
            "status": "ready",
            "target_index": t_idx,
            "sub_index": sub_idx,
        }
        return [card]

    def _inspect_single_post(
        self, shortcode: str, raw_target: str = "", media_type: str = "POST"
    ) -> List[Dict[str, Any]]:
        """Multi-tier post resolution: Public Embed -> Mobile API -> Authenticated Web JSON -> yt-dlp."""
        # Defensive guard: de-concatenate tracking tokens if an un-normalized shortcode reaches this tier
        if len(shortcode) > 13:
            candidate = shortcode[:11]
            if shortcode_to_id(candidate) is not None:
                shortcode = candidate

        target_url = raw_target or f"{IG_BASE_URL}/p/{shortcode}/"

        # Tier 0.5: Instagram Embed Iframe (Resolves public posts without login wall)
        embed_url = f"{IG_BASE_URL}/p/{shortcode}/embed/captioned/"
        opener = get_cookie_opener(self.cookie_file)
        try:
            req = urllib.request.Request(
                embed_url, headers=self._build_headers(require_auth=False)
            )
            with opener.open(req, timeout=10) as resp:
                html_text = resp.read().decode("utf-8", errors="replace")
                card = self._extract_from_embed_html(
                    html_text, shortcode, raw_target=target_url
                )
                if card:
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                    return [card]
        except Exception as exc:
            logger.debug("Embed fallback failed for %s: %s", shortcode, exc)

        # Tier 1: Instagram Mobile Media Info API (Requires valid 64-bit snowflake ID)
        media_id = shortcode_to_id(shortcode)
        if media_id:
            info_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
            headers_mobile = self._build_headers(
                is_mobile=True, require_auth=bool(self.cookie_str)
            )
            res_mobile = self._make_request(
                info_url,
                headers=headers_mobile,
                caller_tag="MobileMediaInfo",
                require_auth=bool(self.cookie_str),
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

        # Tier 2: Authenticated Web JSON Endpoint
        api_url = f"{IG_BASE_URL}/p/{shortcode}/?__a=1&__d=dis"
        res_web = self._make_request(
            api_url,
            caller_tag="WebJSONPost",
            require_auth=bool(self.cookie_str),
        )
        if isinstance(res_web, dict):
            media_data = (
                res_web.get("graphql", {}).get("shortcode_media")
                or res_web.get("data", {}).get("xdt_shortcode_media")
                or (res_web.get("items", [{}])[0] if res_web.get("items") else None)
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

        # Tier 3: yt-dlp Engine Fallback
        self._inspect_via_ytdlp(target_url)
        return []

    def _fetch_timeline_graphql(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Tier 2/3: Paginate user timeline media via POST-based PolarisProfilePostsTimelineQuery."""
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=24)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            if (
                self.max_items_per_profile > 0
                and len(self.seen_ids) >= self.max_items_per_profile
            ):
                logger.info(
                    "Reached safe crawl cap (%d items) for @%s.",
                    self.max_items_per_profile,
                    username,
                )
                break

            variables: Dict[str, Any] = {
                "after": end_cursor if end_cursor else None,
                "first": 24,
                "id": str(user_id),
            }

            payload = {
                "doc_id": DOC_ID_TIMELINE,
                "variables": json.dumps(variables, separators=(",", ":")),
            }
            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-IG-App-ID": IG_APP_ID,
                "X-FB-Friendly-Name": FRIENDLY_NAME_TIMELINE,
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
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Cascading profile crawl with cooldown-guarded fallback transitions."""
        tier_label = (
            "Reels"
            if filter_mode == "reels"
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Tier 1: Web Profile] Crawling {tier_label} for @{username}..."
        )

        cached_profile = self._profile_cache.get(username, {})
        if not cached_profile and not self.is_cancelled:
            url_info = (
                f"{IG_BASE_URL}/api/v1/users/web_profile_info/?username={username}"
            )
            res_info = self._make_request(
                url_info,
                headers={
                    "Referer": f"{IG_BASE_URL}/{username}/",
                    "X-IG-App-ID": IG_APP_ID,
                },
                caller_tag="WebProfileInfo",
                require_auth=bool(self.cookie_str),
                fatal_429=False,
            )
            if res_info and isinstance(res_info, dict):
                cached_profile = (
                    res_info.get("data", {}).get("user") or res_info.get("user") or {}
                )
                if cached_profile:
                    self._profile_cache[username] = cached_profile

        if cached_profile and not self.is_cancelled:
            timeline_obj = cached_profile.get("edge_owner_to_timeline_media") or {}
            edges = timeline_obj.get("edges") if isinstance(timeline_obj, dict) else []

            video_timeline = cached_profile.get("edge_felix_video_timeline") or {}
            video_edges = (
                video_timeline.get("edges") if isinstance(video_timeline, dict) else []
            )
            if isinstance(video_edges, list) and video_edges:
                edges = list(edges) + list(video_edges)

            if isinstance(edges, list) and edges:
                for edge in edges:
                    if self.is_cancelled:
                        return
                    if not isinstance(edge, dict):
                        continue
                    node = (
                        edge.get("node") if isinstance(edge.get("node"), dict) else edge
                    )
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

                    for card in self._extract_media_cards(
                        node, fallback_username=username
                    ):
                        if is_vid and filter_mode == "reels":
                            card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                                self.media_found.emit(card)

                if len(self.seen_ids) > 0:
                    self.status_message.emit(
                        f"✓ [Tier 1: Web Profile] Extracted {len(self.seen_ids)} initial items from profile..."
                    )

        # Tier 2: GraphQL Clips / Timeline Queries
        if (
            self.max_items_per_profile <= 0
            or len(self.seen_ids) < self.max_items_per_profile
        ) and not self.is_cancelled:
            if filter_mode == "reels":
                self.status_message.emit(
                    f"🚀 [Tier 2: GraphQL Clips] Fetching Reels archive for @{username}..."
                )
                reels_found = self._fetch_user_clips_graphql(
                    username, user_id, max_items=24
                )
                if reels_found == 0 and not self.is_cancelled:
                    self._sleep_interruptible(2.5, "Pacing fallback retry")
                    self.status_message.emit(
                        f"🚀 [Tier 2 Fallback: Mobile Clips] Probing mobile clips API for @{username}..."
                    )
                    reels_found = self._fetch_user_clips_mobile(
                        username, user_id, max_items=self.max_items_per_profile
                    )
                if reels_found == 0 and not self.is_cancelled:
                    self._sleep_interruptible(2.5, "Pacing fallback retry")
                    self.status_message.emit(
                        f"🚀 [Tier 2 Fallback: GraphQL Timeline] Searching timeline for @{username}..."
                    )
                    self._fetch_timeline_graphql(username, user_id, filter_mode="reels")
            else:
                self.status_message.emit(
                    f"🚀 [Tier 2: GraphQL Timeline] Fetching timeline posts for @{username}..."
                )
                self._fetch_timeline_graphql(username, user_id, filter_mode=filter_mode)

        # Tier 3: yt-dlp Scrape Fallback
        if len(self.seen_ids) == 0 and not self.is_cancelled:
            self._sleep_interruptible(3.0, "Cooldown before engine fallback")
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/{username}/",
                default_username=username,
                filter_mode=filter_mode,
            )

    def _inspect_via_ytdlp(
        self, url: str, default_username: str = "", filter_mode: str = "all"
    ) -> None:
        """Tier 4: yt-dlp flat extractor fallback with redirect evasion and queue index tagging."""
        if yt_dlp is None:
            msg = "yt-dlp engine is not installed or available in this Python environment."
            logger.warning(msg)
            self.status_message.emit(f"⚠️ {msg}")
            return

        try:
            cfile = self._ensure_cookie_file()
            has_cookies = bool(cfile and os.path.exists(cfile))

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

            is_feed_target = (
                any(
                    f"/{x}/" in clean_url or clean_url.endswith(f"/{x}")
                    for x in ("reels", "stories")
                )
                or default_username != ""
            )

            ydl_opts: Dict[str, Any] = {
                "extract_flat": "in_playlist" if is_feed_target else False,
                "noplaylist": True if not is_feed_target else False,
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

                t_idx = getattr(self, "_current_target_index", 0)

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

                    self._current_sub_index += 1
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
                        "target_index": t_idx,
                        "sub_index": self._current_sub_index,
                    }
                    self.item_found.emit(card)
                    self.media_found.emit(card)
        except Exception as ex:
            logger.debug("yt-dlp fallback error: %s", ex)

    def _fetch_stories_web(
        self, username: str, user_id: str, target_story_id: Optional[str] = None
    ) -> None:
        """Fetches active user stories using authenticated endpoints with web-aligned domain routing."""
        found_any = False
        endpoints = [
            f"{IG_BASE_URL}/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"{IG_BASE_URL}/api/v1/feed/user/{user_id}/story/",
            f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/story/",
        ]

        t_idx = getattr(self, "_current_target_index", 0)

        for ep in endpoints:
            if self.is_cancelled:
                return

            is_mobile_ep = "i.instagram.com" in ep
            headers = self._build_headers(
                referer=f"{IG_BASE_URL}/stories/{username}/",
                require_auth=True,
                is_mobile=is_mobile_ep,
            )

            res = self._make_request(
                ep,
                headers=headers,
                caller_tag="StoriesAPI",
                require_auth=True,
                fatal_429=False,
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
                    item_id = str(item.get("id") or item.get("pk") or "").split("_")[0]
                    if target_story_id and item_id and item_id != target_story_id:
                        continue

                    cards = self._extract_media_cards(item, fallback_username=username)
                    with self._lock:
                        for card in cards:
                            is_vid = bool(card.get("is_video"))
                            cid = str(card["id"])
                            card["media_type"] = "STORY"
                            card["is_video"] = is_vid
                            card["title"] = f"@{username} Story ({idx}/{len(items)})"
                            card["target_index"] = t_idx
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

    def _inspect_single_target(self, raw_target: str) -> None:
        """Inspects an individual target URL across chained tiers with story snowflake resolution."""
        if self.is_cancelled:
            return

        target = parse_instagram_url(raw_target)

        code = target.get("shortcode")
        if (
            target.get("is_share_token")
            or (code and len(code) > 13)
            or "/share/" in raw_target
        ):
            resolved_url = self._resolve_canonical_url(raw_target)
            if resolved_url != raw_target:
                raw_target = resolved_url
                target = parse_instagram_url(raw_target)

        ttype = target.get("type")
        username = target.get("username")
        shortcode = target.get("shortcode")
        t_idx = getattr(self, "_current_target_index", 0)

        # 1. Direct Post / Reel / Carousel
        if ttype in ("reel", "post", "carousel", "tv") and shortcode:
            self._inspect_single_post(shortcode, raw_target=raw_target)

        # 2. Instagram Story Inspection
        elif ttype in ("story", "story_user") and username:
            if shortcode and shortcode.isdigit():
                self.status_message.emit(
                    f"🔍 [Story] Inspecting story media ID #{shortcode} directly..."
                )
                info_url = f"{IG_API_BASE_URL}/media/{shortcode}/info/"
                headers_mobile = self._build_headers(
                    is_mobile=True, require_auth=bool(self.cookie_str)
                )
                res_story = self._make_request(
                    info_url,
                    headers=headers_mobile,
                    caller_tag="MobileStoryMediaInfo",
                    require_auth=bool(self.cookie_str),
                    fatal_429=False,
                )
                if res_story and isinstance(res_story, dict) and res_story.get("items"):
                    extracted = self._extract_media_cards(
                        res_story["items"][0],
                        raw_target=raw_target,
                        fallback_username=username,
                    )
                    if extracted:
                        with self._lock:
                            for card in extracted:
                                is_vid = bool(card.get("is_video"))
                                card["media_type"] = "STORY"
                                card["is_video"] = is_vid
                                card["title"] = f"@{username} Story #{shortcode}"
                                card["target_index"] = t_idx
                                cid = str(card["id"])
                                if cid not in self.seen_ids:
                                    self.seen_ids.add(cid)
                                    self.item_found.emit(card)
                                    self.media_found.emit(card)
                        return

            uid = self._get_user_id(username)
            if uid:
                self._fetch_stories_web(username, uid, target_story_id=shortcode)
            else:
                self._inspect_via_ytdlp(raw_target, default_username=username)

        # 3. Profile Media (Grid, Clips, and Reels Tab)
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
        else:
            self._inspect_via_ytdlp(raw_target)

    def run(self) -> None:
        """Sequential rate-limited execution loop tracking target index per item."""
        try:
            total = len(self.targets)
            if total == 0:
                self.finished.emit(0)
                self.inspection_finished.emit(0)
                return

            self.progress.emit(10)

            for idx, raw_target in enumerate(self.targets):
                if self.is_cancelled:
                    break

                self._current_target_index = idx
                self._current_sub_index = 0

                # Apply adaptive inter-item delay prior to inspecting subsequent items
                if idx > 0:
                    prev_target_info = parse_instagram_url(self.targets[idx - 1])
                    curr_target_info = parse_instagram_url(raw_target)

                    is_prev_direct = prev_target_info.get("type") in (
                        "reel",
                        "post",
                        "carousel",
                        "tv",
                    )
                    is_curr_direct = curr_target_info.get("type") in (
                        "reel",
                        "post",
                        "carousel",
                        "tv",
                    )

                    if is_prev_direct and is_curr_direct:
                        self._apply_direct_item_pacing(idx)
                    else:
                        cooldown = random.uniform(
                            INTER_PROFILE_COOLDOWN_MIN, INTER_PROFILE_COOLDOWN_MAX
                        )
                        self._sleep_interruptible(
                            cooldown,
                            status_msg="⏳ Inter-target profile cooldown",
                        )

                if self.is_cancelled:
                    break

                self.status_message.emit(
                    f"Inspecting ({idx + 1}/{total}): {raw_target}"
                )
                self._inspect_single_target(raw_target)

                pct = int(10 + ((idx + 1) / total) * 85)
                self.progress.emit(pct)

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
