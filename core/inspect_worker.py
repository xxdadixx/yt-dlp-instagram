"""
core/inspect_worker.py - High-speed parallel multi-tier media inspection worker for Instagram.
Features chained multi-tier pagination, detailed system diagnostics, unauthenticated-first routing,
adaptive Gaussian jitter pacing, anti-scraping checkpoint circuit-breakers, and HTML SSR extraction.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import html as html_lib
import json
import logging
import math
import os
import random
import re
import ssl
import threading
import time
from typing import Any, Dict, List, Optional, Set, cast
import urllib.error
import urllib.parse
import urllib.request
import zlib

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
    IG_FEED_USER_URL = "https://i.instagram.com/api/v1/feed/user/{user_id}/"
    IG_CLIPS_USER_URL = "https://i.instagram.com/api/v1/clips/user/"
    IG_USER_INFO_MOBILE_URL = "https://i.instagram.com/api/v1/users/{user_id}/info/"
    MAX_PAGINATION_PAGES = 15

# --- Anti-Scraping Protection & Adaptive Pacing Defaults ---
DEFAULT_MAX_ITEMS_PER_PROFILE = 36

# 1. Profile Crawl Pacing
PROFILE_PAGING_MEAN_DELAY = 2.85
PROFILE_PAGING_STD_DEV = 0.25
MIN_PROFILE_PAGING_DELAY = 2.5
MAX_PROFILE_PAGING_DELAY = 3.2
PROFILE_MACRO_DWELL_INTERVAL = 4
PROFILE_MACRO_DWELL_MIN = 15.0
PROFILE_MACRO_DWELL_MAX = 22.0

# 2. Direct Media Inspection Pacing
DIRECT_INSPECT_MEAN_DELAY = 0.85
DIRECT_INSPECT_STD_DEV = 0.15
MIN_DIRECT_INSPECT_DELAY = 0.60
MAX_DIRECT_INSPECT_DELAY = 1.25
DIRECT_MACRO_DWELL_INTERVAL = 36
DIRECT_MICRO_REST_MIN = 3.0
DIRECT_MICRO_REST_MAX = 5.0

# 3. Inter-Profile Cooldown
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

# GraphQL Persisted Query Identifiers & Known Fallbacks
FRIENDLY_NAME_CLIPS = "PolarisClipsTimelineProfileQuery"
DOC_ID_USER_CLIPS = "8677440618991207"
DOC_ID_USER_CLIPS_FALLBACKS: tuple[str, ...] = (
    "8677440618991207",
    "7659599557452668",
    "7659222234144632",
    "7350709088371300",
    "6966144886812836",
)

FRIENDLY_NAME_TIMELINE = "PolarisProfilePostsTimelineQuery"
DOC_ID_TIMELINE = "8552168934898494"
DOC_ID_TIMELINE_FALLBACKS: tuple[str, ...] = (
    "8552168934898494",
    "9310860572338573",
    "7095914977196024",
    "6966144886812836",
    "6047242945377598",
)

FRIENDLY_NAME_PROFILE_INFO = "PolarisProfilePageHeaderQuery"
DOC_ID_PROFILE_INFO = "6047242945377598"

logger = logging.getLogger("InspectWorker")


class YTDLPQuietLogger:
    """Redirects yt-dlp internal telemetry into debug output."""

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.debug("[yt-dlp warning] %s", msg)

    def error(self, msg: str) -> None:
        logger.warning("[yt-dlp error] %s", msg)

    def info(self, msg: str) -> None:
        pass


def _find_graphql_nodes_recursive(
    obj: Any, max_depth: int = 18
) -> list[dict[str, Any]]:
    """Traverses arbitrary JSON/Relay trees to discover Instagram media node objects."""
    if max_depth <= 0:
        return []
    nodes: list[dict[str, Any]] = []

    if isinstance(obj, dict):
        is_media_node = (
            ("shortcode" in obj or "code" in obj)
            and ("id" in obj or "pk" in obj)
            and any(
                k in obj
                for k in (
                    "display_url",
                    "video_url",
                    "image_versions2",
                    "video_versions",
                    "edge_media_to_caption",
                    "taken_at_timestamp",
                    "media_type",
                )
            )
        )
        if is_media_node:
            nodes.append(obj)
        else:
            for val in obj.values():
                if isinstance(val, (dict, list)):
                    nodes.extend(_find_graphql_nodes_recursive(val, max_depth - 1))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                nodes.extend(_find_graphql_nodes_recursive(item, max_depth - 1))

    return nodes


def _extract_nodes_from_html(html_text: str) -> list[dict[str, Any]]:
    """Parses JSON script blocks from SSR HTML markup and unescapes DOM text."""
    nodes: list[dict[str, Any]] = []
    if not html_text:
        return nodes

    unescaped = html_lib.unescape(html_text)

    # 1. Parse JSON inside script tags
    for match in re.finditer(
        r"<script[^>]*>(.*?)</script>",
        unescaped,
        re.DOTALL | re.IGNORECASE,
    ):
        raw_script = match.group(1).strip()
        if not raw_script or (
            "shortcode" not in raw_script and "code" not in raw_script
        ):
            continue

        if (raw_script.startswith("{") and raw_script.endswith("}")) or (
            raw_script.startswith("[") and raw_script.endswith("]")
        ):
            try:
                parsed = json.loads(raw_script)
                extracted = _find_graphql_nodes_recursive(parsed)
                if extracted:
                    nodes.extend(extracted)
                    continue
            except Exception:
                pass

        for json_match in re.finditer(
            r'\{[^{}]*"(?:shortcode|code)"[^{}]*\}', raw_script
        ):
            try:
                obj = json.loads(json_match.group(0))
                if isinstance(obj, dict):
                    nodes.append(obj)
            except Exception:
                continue

    # 2. Extract legacy window._sharedData and __additionalDataLoaded payloads
    for pattern in (
        r"window\._sharedData\s*=\s*(\{.+?\});",
        r"window\.__additionalDataLoaded\([^,]+,\s*(\{.+?\})\s*\);",
    ):
        for m in re.finditer(pattern, unescaped, re.DOTALL):
            try:
                parsed = json.loads(m.group(1))
                extracted = _find_graphql_nodes_recursive(parsed)
                if extracted:
                    nodes.extend(extracted)
            except Exception:
                continue

    return nodes


def _extract_shortcodes_from_html(html_text: str) -> list[str]:
    """Extracts post and reel shortcodes from SSR HTML markup, escaped strings, and entity attributes."""
    if not html_text:
        return []

    unescaped = html_lib.unescape(html_text)
    candidates: list[str] = []

    # 1. Unescaped and escaped URL paths: /p/CODE/, /reel/CODE/, \/reel\/CODE\/
    candidates.extend(
        re.findall(
            r'(?:/|\\/)(?:p|reel|reels)(?:/|\\/)([a-zA-Z0-9_\-]{9,13})(?:[/?\\"\s&]|$)',
            unescaped,
            re.IGNORECASE,
        )
    )

    # 2. JSON key-value pairs (supports "shortcode":"CODE", "code":"CODE", \"code\":\"CODE\", etc.)
    candidates.extend(
        re.findall(
            r'["\\\'](?:shortcode|code)["\\\']\s*:\s*["\\\']([a-zA-Z0-9_\-]{9,13})["\\\']',
            unescaped,
            re.IGNORECASE,
        )
    )

    seen: set[str] = set()
    ordered_codes: list[str] = []
    reserved: frozenset[str] = frozenset(
        {
            "reels",
            "reel",
            "feed",
            "stories",
            "explore",
            "channel",
            "tagged",
            "audio",
            "direct",
            "highlights",
        }
    )

    for code in candidates:
        cleaned = code.strip()
        if (
            cleaned not in seen
            and cleaned.lower() not in reserved
            and not cleaned.isdigit()
            and len(cleaned) >= 9
        ):
            seen.add(cleaned)
            ordered_codes.append(cleaned)

    return ordered_codes


class InspectWorker(QThread):
    progress = pyqtSignal(int)
    item_found = pyqtSignal(dict)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)
    media_found = pyqtSignal(dict)
    inspection_finished = pyqtSignal(int)
    error_occurred = pyqtSignal(str)

    MAX_CONCURRENT_INSPECTS: int = 1

    _find_graphql_nodes_recursive = staticmethod(_find_graphql_nodes_recursive)
    _extract_shortcodes_from_html = staticmethod(_extract_shortcodes_from_html)
    _extract_nodes_from_html = staticmethod(_extract_nodes_from_html)

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
        if self.max_items_per_profile <= 0:
            return 1000
        needed_pages = math.ceil(self.max_items_per_profile / page_size) + 2
        return max(MAX_PAGINATION_PAGES, needed_pages)

    def cancel(self) -> None:
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
            logger.debug("Failed to ensure cookie file: %s", e)
        return None

    def _sleep_interruptible(
        self, duration: float, status_msg: Optional[str] = None
    ) -> None:
        start_t = time.time()
        last_emitted_sec: int = -1
        while time.time() - start_t < duration:
            if self.is_cancelled:
                break
            if status_msg:
                rem = max(0.0, duration - (time.time() - start_t))
                rem_sec = int(math.ceil(rem))
                if rem_sec != last_emitted_sec and rem_sec > 0:
                    last_emitted_sec = rem_sec
                    self.status_message.emit(f"{status_msg} ({rem_sec}s remaining)...")
            time.sleep(0.1)

    def _apply_gaussian_pacing(self) -> None:
        if self.is_cancelled:
            return
        delay = random.gauss(PROFILE_PAGING_MEAN_DELAY, PROFILE_PAGING_STD_DEV)
        sleep_time = max(MIN_PROFILE_PAGING_DELAY, min(delay, MAX_PROFILE_PAGING_DELAY))
        self._sleep_interruptible(sleep_time)

    def _apply_macro_pacing(self, page_number: int) -> None:
        if self.is_cancelled or page_number <= 0:
            return

        if page_number % 12 == 0:
            deep_rest = random.uniform(40.0, 60.0)
            self._sleep_interruptible(
                deep_rest,
                status_msg=f"🛡️ Deep crawl velocity cooldown (page {page_number})",
            )
            return

        if page_number % PROFILE_MACRO_DWELL_INTERVAL == 0:
            rest_seconds = random.uniform(
                PROFILE_MACRO_DWELL_MIN, PROFILE_MACRO_DWELL_MAX
            )
            self._sleep_interruptible(
                rest_seconds,
                status_msg=f"☕ Natural dwell rest (page {page_number})",
            )

    def _apply_direct_item_pacing(self, item_index: int) -> None:
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

    def _apply_tier_backoff(
        self,
        tier_attempt: int,
        base: float = 2.0,
        max_delay: float = 6.0,
        is_throttled: bool = False,
    ) -> None:
        if self.is_cancelled:
            return

        effective_base = base * (2.5 if is_throttled else 1.0)
        effective_max = max_delay * (2.0 if is_throttled else 1.0)
        upper_bound = min(effective_max, effective_base * (2.0 ** min(tier_attempt, 3)))
        delay = random.uniform(effective_base, max(effective_base + 0.5, upper_bound))

        self._sleep_interruptible(delay, status_msg="🛡️ Pacing tier fallback transition")

    def _build_headers(
        self,
        referer: str = "https://www.instagram.com/",
        require_auth: bool = False,
        is_mobile: bool = False,
        is_document: bool = False,
    ) -> dict[str, str]:
        if is_mobile:
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
            if require_auth and self.cookie_str:
                headers["Cookie"] = self.cookie_str
                if self._csrf_token:
                    headers["X-CSRFToken"] = self._csrf_token
            return headers

        if is_document:
            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
                "Referer": referer,
            }
        else:
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
        self,
        response_url: str,
        response_text: str,
        status_code: int,
        fatal_429: bool = False,
    ) -> bool:
        checkpoint_indicators = (
            "/accounts/scraping_warning/",
            "checkpoint_required",
            "checkpoint_url",
            "challenge_required",
            "challenge_context",
            "consent_required",
        )

        final_url = response_url.lower()

        if any(ind in final_url for ind in checkpoint_indicators):
            logger.error("Account security checkpoint detected in URL: %s", final_url)
            self.resilient_session.trip_circuit_breaker(
                f"Checkpoint redirect: {final_url}"
            )
            self.status_message.emit(
                "🛑 Account checkpoint triggered. Halting to protect account."
            )
            self.cancel()
            return False

        # Only check payload strings if status indicates an error or an explicit JSON response
        if not (200 <= status_code < 300) or response_text.strip().startswith("{"):
            lowered_text = response_text.lower()
            for ind in checkpoint_indicators:
                if f'"{ind}"' in lowered_text or f"'{ind}'" in lowered_text:
                    logger.error("Hard checkpoint challenge in payload: %s", ind)
                    self.resilient_session.trip_circuit_breaker(
                        f"Checkpoint in payload ({ind})"
                    )
                    self.status_message.emit(
                        "🛑 Account challenge triggered. Halting to protect your account."
                    )
                    self.cancel()
                    return False

            soft_pushbacks = (
                "feedback_required",
                "is_spam",
                "action_blocked",
                "please wait a few minutes",
            )
            if any(ind in lowered_text for ind in soft_pushbacks):
                logger.warning("Soft velocity throttle on endpoint %s.", response_url)
                return False

        if status_code == 429:
            logger.warning("HTTP 429 Rate Limit on endpoint %s", response_url)
            if fatal_429:
                self.resilient_session.trip_circuit_breaker(
                    "HTTP 429 Rate Limit encountered"
                )
                self.status_message.emit(
                    "⚠️ HTTP 429. Halting inspection to protect account."
                )
                self.cancel()
            return False

        if "/accounts/login/" in final_url or status_code in (401, 403):
            return False

        return True

    def _make_request(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        caller_tag: str = "",
        require_auth: bool = False,
        fatal_429: bool = False,
    ) -> Optional[dict[str, Any]]:
        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return None

        req_headers = self._build_headers(require_auth=require_auth)
        if headers:
            req_headers.update(headers)

        req_method = method or ("POST" if data is not None else "GET")

        try:
            status_code, final_url, resp_headers, text = self.resilient_session.request(
                method=req_method,
                url=url,
                headers=req_headers,
                data=data,
                timeout=float(timeout),
                require_auth=require_auth,
            )

            if "csrftoken" in self.resilient_session.cookies and not self._csrf_token:
                self._csrf_token = self.resilient_session.cookies["csrftoken"]

            if not self._is_safe_response(
                final_url, text, status_code, fatal_429=fatal_429
            ):
                return None

            if not (200 <= status_code < 300):
                logger.debug(
                    "[%s] HTTP %d returned for %s",
                    caller_tag or "API",
                    status_code,
                    url,
                )
                return None

            clean_text = text.lstrip("\ufeff").strip()
            if clean_text.startswith(("{", "[")):
                try:
                    parsed_json = json.loads(clean_text)
                    return (
                        parsed_json if isinstance(parsed_json, (dict, list)) else None
                    )
                except json.JSONDecodeError:
                    return None
            return None

        except PermissionError as pe:
            logger.error("🛑 [%s] Security Tripwire: %s", caller_tag or "API", pe)
            self.status_message.emit(f"🛑 [Security Alert] {pe}")
            self.cancel()
            return None
        except Exception as exc:
            logger.debug(
                "[%s] Transport error for %s: %s", caller_tag or "API", url, exc
            )
            return None

    def _extract_profile_data_from_html(
        self, html_text: str, username: str
    ) -> Optional[Dict[str, Any]]:
        if not html_text:
            return None

        for match in re.finditer(
            r'<script\s+type="application/json"[^>]*>(.*?)</script>',
            html_text,
            re.DOTALL,
        ):
            content = match.group(1).strip()
            if (
                "edge_owner_to_timeline_media" in content
                or "xdt_api__v1__feed__user_timeline" in content
            ):
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        user_obj = (
                            data.get("data", {}).get("user")
                            or data.get("graphql", {}).get("user")
                            or data.get("user")
                        )
                        if isinstance(user_obj, dict):
                            return user_obj
                except Exception:
                    continue

        shared_data_match = re.search(
            r"window\._sharedData\s*=\s*(\{.+?\});</script>", html_text, re.DOTALL
        )
        if shared_data_match:
            try:
                data = json.loads(shared_data_match.group(1))
                entry_data = data.get("entry_data", {}).get("ProfilePage", [])
                if (
                    isinstance(entry_data, list)
                    and entry_data
                    and isinstance(entry_data[0], dict)
                ):
                    user_obj = entry_data[0].get("graphql", {}).get("user")
                    if isinstance(user_obj, dict):
                        return user_obj
            except Exception:
                pass

        return None

    def _resolve_canonical_url(self, target_url: str) -> str:
        if (
            not target_url
            or self.is_cancelled
            or self.resilient_session.is_circuit_open
        ):
            return target_url

        self.status_message.emit("🔗 Resolving share token / canonical redirect...")

        nav_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            status_code, final_url, headers, html_text = self.resilient_session.request(
                "GET",
                target_url,
                headers=nav_headers,
                timeout=10.0,
            )

            if final_url != target_url and not any(
                x in final_url for x in ("/accounts/login/", "/accounts/")
            ):
                parsed_final = parse_instagram_url(final_url)
                code = parsed_final.get("shortcode")
                if parsed_final.get("valid") and code and len(code) <= 13:
                    return final_url

            if "/accounts/login/" in final_url:
                parsed_login = urllib.parse.urlparse(final_url)
                next_params = urllib.parse.parse_qs(parsed_login.query).get("next", [])
                if next_params:
                    next_path = urllib.parse.unquote(next_params[0])
                    candidate = urllib.parse.urljoin(IG_BASE_URL, next_path)
                    parsed_candidate = parse_instagram_url(candidate)
                    c_code = parsed_candidate.get("shortcode")
                    if parsed_candidate.get("valid") and c_code and len(c_code) <= 13:
                        return candidate

            if html_text:
                og_match = re.search(
                    r'<meta\s+property=["\']og:url["\']\s+content=["\'](https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_\-]+)/?)[^"\']*["\']',
                    html_text,
                    re.IGNORECASE,
                )
                if og_match and len(og_match.group(2)) <= 13:
                    return og_match.group(1).rstrip("/") + "/"

                canonical_match = re.search(
                    r'<link\s+rel=["\']canonical["\']\s+href=["\'](https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/([a-zA-Z0-9_\-]+)/?)[^"\']*["\']',
                    html_text,
                    re.IGNORECASE,
                )
                if canonical_match and len(canonical_match.group(2)) <= 13:
                    return canonical_match.group(1).rstrip("/") + "/"

        except Exception as exc:
            logger.debug(
                "Redirect resolution encountered error for %s: %s", target_url, exc
            )

        parsed_target = parse_instagram_url(target_url)
        raw_code = parsed_target.get("shortcode")
        if raw_code and len(raw_code) > 13:
            candidate_code = raw_code[:11]
            if shortcode_to_id(candidate_code) is not None:
                media_path = "reel" if parsed_target.get("type") == "reel" else "p"
                return f"{IG_BASE_URL}/{media_path}/{candidate_code}/"

        return target_url

    def _get_user_id(self, username: str) -> Optional[str]:
        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return None

        clean_user = username.lower().strip().lstrip("@")
        self.status_message.emit(f"🔍 [Resolver] Fetching User ID for @{clean_user}...")

        # 1. HTML Profile Scraper
        try:
            profile_url = f"{IG_BASE_URL}/{clean_user}/"
            status_code, _, _, html_text = self.resilient_session.request(
                method="GET",
                url=profile_url,
                headers=self._build_headers(referer=IG_BASE_URL, require_auth=False),
                timeout=10.0,
                require_auth=False,
            )
            if status_code == 200 and html_text:
                embedded_user = self._extract_profile_data_from_html(
                    html_text, clean_user
                )
                if embedded_user:
                    self._profile_cache[clean_user] = embedded_user

                patterns = [
                    r'"user_id":"(\d+)"',
                    r'"owner":\{"id":"(\d+)"',
                    r'"profile_id":"(\d+)"',
                    r'"user":\{"id":"(\d+)"',
                    r'"id":"(\d+)","username":"' + re.escape(clean_user) + r'"',
                    r'"pk":"?(\d+)"?',
                ]
                for pat in patterns:
                    m = re.search(pat, html_text)
                    if m:
                        uid = m.group(1)
                        self.status_message.emit(
                            f"✓ [Resolver] Found User ID: {uid} (@{clean_user})"
                        )
                        return uid
        except Exception as e:
            logger.debug("HTML scraper resolver error: %s", e)

        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return None

        self._sleep_interruptible(random.uniform(0.3, 0.6))

        # 2. Public Search Query
        try:
            url_search = f"{IG_BASE_URL}/web/search/topsearch/?query={clean_user}"
            res_search = self._make_request(
                url_search,
                headers={"Referer": f"{IG_BASE_URL}/{clean_user}/"},
                caller_tag="TopSearch",
                require_auth=False,
                fatal_429=False,
            )
            if res_search and isinstance(res_search, dict) and "users" in res_search:
                for item in res_search["users"]:
                    u = item.get("user") or {}
                    if str(u.get("username", "")).lower() == clean_user:
                        uid = u.get("pk") or u.get("id")
                        if uid:
                            self.status_message.emit(
                                f"✓ [Resolver] Found User ID via Search: {uid} (@{clean_user})"
                            )
                            return str(uid)
        except Exception as e:
            logger.debug("TopSearch resolver error: %s", e)

        return None

    def _fetch_user_clips_web(
        self, username: str, user_id: str, max_items: int = 24
    ) -> int:
        """Paginates user Reels archive using Instagram Web's native REST endpoint."""
        url = f"{IG_BASE_URL}/api/v1/clips/user/"
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=max_items)

        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return found_count

        headers = self._build_headers(
            referer=f"{IG_BASE_URL}/{username}/reels/",
            require_auth=bool(self.cookie_str),
            is_mobile=False,
        )
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        has_next_page = True
        max_id: Optional[str] = None
        pages = 0

        while has_next_page and pages < max_pages and not self.is_cancelled:
            with self._lock:
                if (
                    self.max_items_per_profile > 0
                    and len(self.seen_ids) >= self.max_items_per_profile
                ):
                    return found_count

            post_params: dict[str, Any] = {
                "target_user_id": str(user_id),
                "page_size": str(max_items),
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
                caller_tag="WebClipsAPI",
                require_auth=bool(self.cookie_str),
                fatal_429=False,
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
            with self._lock:
                total_seen = len(self.seen_ids)
            self.status_message.emit(
                f"✓ [Web Clips] Page {pages}: {total_seen} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_user_feed_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user grid media using Instagram Web's native feed endpoint."""
        found_count = 0
        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return found_count

        uid_str = str(user_id).strip()
        if not uid_str or not uid_str.isdigit():
            return found_count

        headers = self._build_headers(
            referer=f"{IG_BASE_URL}/{username}/",
            require_auth=bool(self.cookie_str),
            is_mobile=False,
        )

        has_next_page = True
        next_max_id: Optional[str] = None
        pages = 0
        max_pages = self._get_max_pages_ceiling(page_size=24)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            with self._lock:
                if (
                    self.max_items_per_profile > 0
                    and len(self.seen_ids) >= self.max_items_per_profile
                ):
                    break

            url = f"{IG_BASE_URL}/api/v1/feed/user/{uid_str}/?count=24"
            if next_max_id:
                url += f"&max_id={urllib.parse.quote(str(next_max_id))}"

            res = self._make_request(
                url,
                headers=headers,
                caller_tag="WebUserFeed",
                require_auth=bool(self.cookie_str),
                fatal_429=False,
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

            has_next_page = bool(
                res.get("more_available", False)
                or res.get("auto_load_more_enabled", False)
            )
            raw_cursor = res.get("next_max_id") or res.get("max_id")
            next_max_id = str(raw_cursor).strip() if raw_cursor else None

            if not next_max_id:
                has_next_page = False

            pages += 1
            with self._lock:
                total_seen = len(self.seen_ids)
            self.status_message.emit(
                f"✓ [Web Feed] Page {pages}: {total_seen} items found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_user_clips_graphql(
        self, username: str, user_id: str, max_items: int = 24
    ) -> int:
        """Paginates user Reels archive using PolarisClipsTimelineProfileQuery."""
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=max_items)

        uid_str = str(user_id).strip()
        if not uid_str or not uid_str.isdigit():
            return 0

        if not self.resilient_session.has_session_cookies():
            return 0

        numeric_uid = int(uid_str)

        while has_next_page and pages < max_pages and not self.is_cancelled:
            with self._lock:
                if (
                    self.max_items_per_profile > 0
                    and len(self.seen_ids) >= self.max_items_per_profile
                ):
                    break

            variables: dict[str, Any] = {
                "data": {
                    "include_feed_video": True,
                    "page_size": max_items,
                    "target_user_id": numeric_uid,
                }
            }
            if end_cursor:
                variables["data"]["max_id"] = end_cursor
                variables["after"] = end_cursor

            res: Optional[dict[str, Any]] = None
            for doc_id in DOC_ID_USER_CLIPS_FALLBACKS:
                if self.is_cancelled or self.resilient_session.is_circuit_open:
                    return found_count

                try:
                    res = self.resilient_session.execute_persisted_query(
                        doc_id=doc_id,
                        variables=variables,
                        friendly_name=FRIENDLY_NAME_CLIPS,
                    )
                    if isinstance(res, dict) and res.get("data"):
                        break
                except PermissionError as pe:
                    self.status_message.emit(f"🛑 [Security Alert] {pe}")
                    self.cancel()
                    return found_count
                except Exception as exc:
                    logger.debug("[GraphQLClips] doc_id=%s fault: %s", doc_id, exc)

            if not isinstance(res, dict):
                break

            data_root = res.get("data", {}) if isinstance(res, dict) else {}
            clips_conn = (
                data_root.get("xdt_api__v1__clips__user__connection_v2")
                or data_root.get("xdt_api__v1__clips__user__connection")
                or data_root.get("user", {}).get("edge_felix_video_timeline")
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
                end_cursor = None

            if not end_cursor:
                has_next_page = False

            pages += 1
            with self._lock:
                total_seen = len(self.seen_ids)
            self.status_message.emit(
                f"✓ [GraphQL Clips] Page {pages}: {total_seen} Reels found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_timeline_graphql(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user timeline media using PolarisProfilePostsTimelineQuery."""
        has_next_page = True
        end_cursor: str | None = None
        pages = 0
        found_count = 0
        max_pages = self._get_max_pages_ceiling(page_size=24)

        if not self.resilient_session.has_session_cookies():
            return 0

        uid_str = str(user_id).strip()

        while has_next_page and pages < max_pages and not self.is_cancelled:
            with self._lock:
                if (
                    self.max_items_per_profile > 0
                    and len(self.seen_ids) >= self.max_items_per_profile
                ):
                    break

            variables: dict[str, Any] = {
                "data": {
                    "count": 24,
                    "include_relationship_info": True,
                    "latest_besties_reel_media": True,
                    "latest_reel_media": True,
                },
                "username": username,
                "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
            }
            if end_cursor:
                variables["after"] = end_cursor

            res: dict[str, Any] | None = None
            for doc_id in DOC_ID_TIMELINE_FALLBACKS:
                if self.is_cancelled or self.resilient_session.is_circuit_open:
                    return found_count

                try:
                    res = self.resilient_session.execute_persisted_query(
                        doc_id=doc_id,
                        variables=variables,
                        friendly_name=FRIENDLY_NAME_TIMELINE,
                    )
                    if isinstance(res, dict) and res.get("data"):
                        break
                except PermissionError as pe:
                    self.status_message.emit(f"🛑 [Security Alert] {pe}")
                    self.cancel()
                    return found_count
                except Exception as exc:
                    logger.debug("[GraphQLTimeline] doc_id=%s fault: %s", doc_id, exc)

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
                user_node = data_obj.get("user")
                if isinstance(user_node, dict):
                    user_data = user_node.get("edge_owner_to_timeline_media")

            if not isinstance(user_data, dict):
                break

            edges = user_data.get("edges")
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

            page_info = user_data.get("page_info")
            if isinstance(page_info, dict):
                has_next_page = bool(page_info.get("has_next_page", False))
                end_cursor = page_info.get("end_cursor")
            else:
                has_next_page = False
                end_cursor = None

            pages += 1
            with self._lock:
                total_seen = len(self.seen_ids)
            self.status_message.emit(
                f"✓ [GraphQL Timeline] Page {pages}: {total_seen} items found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()
                self._apply_macro_pacing(pages)

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Cascading profile crawler prioritizing SSR HTML extraction, Web REST APIs, and fallbacks."""
        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return

        is_reels_focus = filter_mode == "reels"
        tier_label = (
            "Reels"
            if is_reels_focus
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Crawl Engine] Inspecting {tier_label} for @{username}..."
        )

        initial_count = len(self.seen_ids)

        def _is_limit_reached() -> bool:
            if self.is_cancelled or self.resilient_session.is_circuit_open:
                return True
            with self._lock:
                return (
                    self.max_items_per_profile > 0
                    and len(self.seen_ids) >= self.max_items_per_profile
                )

        # =========================================================================
        # TIER 1: HTML Document Sweep of Target & Root (Immediate Local Resolution)
        # =========================================================================
        try:
            self.status_message.emit(
                f"🚀 [Tier 1: HTML Markup] Scanning page markup for @{username}..."
            )
            probe_urls = (
                [f"{IG_BASE_URL}/{username}/reels/", f"{IG_BASE_URL}/{username}/"]
                if is_reels_focus
                else [f"{IG_BASE_URL}/{username}/", f"{IG_BASE_URL}/{username}/reels/"]
            )

            for p_url in probe_urls:
                if _is_limit_reached():
                    break
                status_code, _, _, html_text = self.resilient_session.request(
                    method="GET",
                    url=p_url,
                    headers=self._build_headers(
                        referer=IG_BASE_URL,
                        require_auth=bool(self.cookie_str),
                        is_document=True,
                    ),
                    timeout=12.0,
                    require_auth=bool(self.cookie_str),
                )
                if 200 <= status_code < 300 and html_text:
                    direct_nodes = self._extract_nodes_from_html(html_text)
                    for node in direct_nodes:
                        if _is_limit_reached():
                            break
                        is_vid = (
                            is_standalone_video(node)
                            or bool(node.get("is_video"))
                            or node.get("media_type") == 2
                            or node.get("product_type") == "clips"
                        )
                        if is_reels_focus and not is_vid:
                            continue
                        if filter_mode == "photos" and is_vid:
                            continue

                        for card in self._extract_media_cards(
                            node, fallback_username=username
                        ):
                            if is_vid and is_reels_focus:
                                card["media_type"] = "REEL"
                            with self._lock:
                                cid = str(card["id"])
                                if cid not in self.seen_ids:
                                    self.seen_ids.add(cid)
                                    self.item_found.emit(card)
                                    self.media_found.emit(card)

                    shortcodes = self._extract_shortcodes_from_html(html_text)
                    if shortcodes:
                        limit = (
                            (self.max_items_per_profile - len(self.seen_ids))
                            if self.max_items_per_profile > 0
                            else len(shortcodes)
                        )
                        for sc in shortcodes[:limit]:
                            if _is_limit_reached():
                                break
                            self._inspect_single_post(
                                sc,
                                raw_target=(
                                    f"{IG_BASE_URL}/reel/{sc}/"
                                    if is_reels_focus
                                    else f"{IG_BASE_URL}/p/{sc}/"
                                ),
                                filter_mode=filter_mode,
                                fallback_username=username,
                            )
                            self._sleep_interruptible(random.uniform(0.2, 0.5))
        except Exception as exc:
            logger.debug("Tier 1 HTML markup fault: %s", exc)

        if _is_limit_reached():
            return

        # =========================================================================
        # TIER 2: Native Web REST APIs (Deep Pagination for Archives)
        # =========================================================================
        try:
            if is_reels_focus:
                self.status_message.emit(
                    f"🚀 [Tier 2: Web Clips API] Paginating Reels archive for @{username}..."
                )
                c_count = self._fetch_user_clips_web(username, user_id, max_items=24)
                logger.info(
                    "Tier 2 Web Clips extracted %d reels for @%s", c_count, username
                )
            else:
                self.status_message.emit(
                    f"🚀 [Tier 2: Web Feed API] Paginating media archive for @{username}..."
                )
                f_count = self._fetch_user_feed_web(
                    username, user_id, filter_mode=filter_mode
                )
                logger.info(
                    "Tier 2 Web Feed extracted %d items for @%s", f_count, username
                )

                if filter_mode == "all" and not _is_limit_reached():
                    self._fetch_user_clips_web(username, user_id, max_items=24)
        except Exception as exc:
            logger.debug("Tier 2 Web REST API fault: %s", exc)

        if _is_limit_reached() or (
            len(self.seen_ids) > initial_count and self.max_items_per_profile > 0
        ):
            return

        # =========================================================================
        # TIER 3: GraphQL Persisted Query Fallback
        # =========================================================================
        if self.resilient_session.has_session_cookies():
            self._apply_tier_backoff(tier_attempt=1, base=1.2, max_delay=2.0)
            try:
                if is_reels_focus:
                    self.status_message.emit(
                        f"🚀 [Tier 3: GraphQL Clips] Querying Reels archive for @{username}..."
                    )
                    self._fetch_user_clips_graphql(username, user_id, max_items=24)
                else:
                    self.status_message.emit(
                        f"🚀 [Tier 3: GraphQL Timeline] Paginating grid media for @{username}..."
                    )
                    self._fetch_timeline_graphql(
                        username, user_id, filter_mode=filter_mode
                    )
            except Exception as exc:
                logger.debug("Tier 3 GraphQL fault: %s", exc)

        if len(self.seen_ids) > initial_count or self.is_cancelled:
            return

        # =========================================================================
        # TIER 4: Engine Fallback (yt-dlp)
        # =========================================================================
        self._apply_tier_backoff(tier_attempt=2, base=2.0, max_delay=3.5)
        try:
            self.status_message.emit("⚙️ [Tier 4: Engine Fallback] Running yt-dlp...")
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/{username}/",
                default_username=username,
                filter_mode=filter_mode,
            )
        except Exception as exc:
            logger.debug("Tier 4 yt-dlp fallback error: %s", exc)

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
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

        # 1. Multi-Item Carousel
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

            return [
                {
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
            ]

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

        return [
            {
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
                "view_count": int(
                    media.get("view_count") or media.get("play_count") or 0
                ),
                "like_count": int(media.get("like_count") or 0),
                "media_type": b_type,
                "is_video": is_vid,
                "quality": self.quality_preset,
                "selected": True,
                "status": "ready",
                "target_index": t_idx,
                "sub_index": sub_idx,
            }
        ]

    def _extract_from_embed_html(
        self,
        html_text: str,
        shortcode: str,
        raw_target: str = "",
        fallback_username: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not html_text:
            return None

        media_data: Optional[Dict[str, Any]] = None

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
            except Exception:
                pass

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

        if not media_data:
            img_match = (
                re.search(
                    r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                    html_text,
                    re.IGNORECASE,
                )
                or re.search(
                    r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\']',
                    html_text,
                    re.IGNORECASE,
                )
                or re.search(
                    r'<img[^>]+src=["\'](https?://[^"\']*(?:cdninstagram\.com|fbcdn\.net)[^"\']*)["\']',
                    html_text,
                    re.IGNORECASE,
                )
            )

            thumb_url = img_match.group(1).replace("&amp;", "&") if img_match else ""

            video_match = re.search(
                r'<video[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE
            ) or re.search(
                r'<source[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE
            )
            extracted_video_url = (
                video_match.group(1).replace("&amp;", "&") if video_match else ""
            )

            is_video = bool(
                extracted_video_url
                or "EmbeddedMediaVideo" in html_text
                or "video_url" in html_text
                or "/reel/" in raw_target.lower()
                or "/reels/" in raw_target.lower()
            )

            detected_user = ""
            cap_user_match = re.search(
                r'<a[^>]+class=["\'][^"\']*CaptionUsername[^"\']*["\'][^>]*>\s*@?([a-zA-Z0-9_\.]+)\s*</a>',
                html_text,
                re.IGNORECASE,
            )
            if cap_user_match:
                detected_user = cap_user_match.group(1).strip()

            if not detected_user:
                header_href = re.search(
                    r'<a[^>]+href=["\'](?:https?://(?:www\.)?instagram\.com)?/([a-zA-Z0-9_\.]+)/?(?:\?[^"\']*)?["\'][^>]*class=["\'][^"\']*(?:Username|Avatar|Header|CaptionUsername)[^"\']*["\']',
                    html_text,
                    re.IGNORECASE,
                )
                if header_href:
                    candidate = header_href.group(1).strip()
                    if candidate.lower() not in (
                        "p",
                        "reel",
                        "reels",
                        "tv",
                        "stories",
                        "explore",
                    ):
                        detected_user = candidate

            if not detected_user or detected_user.lower() in ("instagram", "p", "reel"):
                detected_user = fallback_username or "instagram"

            caption_match = re.search(
                r'<div[^>]+class=["\'][^"\']*Caption[^"\']*["\'][^>]*>(.*?)</div>',
                html_text,
                re.DOTALL | re.IGNORECASE,
            )
            raw_caption = ""
            if caption_match:
                raw_caption = re.sub(r"<[^>]+>", "", caption_match.group(1)).strip()
                raw_caption = html_lib.unescape(raw_caption)

            b_type = "REEL" if is_video else "IMAGE"
            first_line = (
                raw_caption.splitlines()[0].strip()
                if raw_caption
                else f"Instagram {b_type} #{shortcode}"
            )

            if is_video and extracted_video_url:
                effective_download_url = extracted_video_url
            elif thumb_url:
                effective_download_url = thumb_url
            else:
                effective_download_url = raw_target or f"{IG_BASE_URL}/p/{shortcode}/"

            self._current_sub_index += 1
            return {
                "id": shortcode,
                "shortcode": shortcode,
                "title": first_line,
                "username": detected_user,
                "url": raw_target or f"{IG_BASE_URL}/p/{shortcode}/",
                "thumbnail_url": thumb_url,
                "video_url": extracted_video_url,
                "download_url": effective_download_url,
                "caption": raw_caption,
                "duration": 0.0,
                "view_count": 0,
                "like_count": 0,
                "media_type": b_type,
                "is_video": is_video,
                "quality": self.quality_preset,
                "selected": True,
                "status": "ready",
                "target_index": getattr(self, "_current_target_index", 0),
                "sub_index": self._current_sub_index,
            }

        if media_data and isinstance(media_data, dict):
            cards = self._extract_media_cards(
                media_data,
                raw_target=raw_target,
                fallback_username=fallback_username,
            )
            if cards:
                card = dict(cards[0])
                if fallback_username and card.get("username") in ("", "instagram"):
                    card["username"] = fallback_username
                return card

        return None

    def _inspect_single_post(
        self,
        shortcode: str,
        raw_target: str = "",
        media_type: str = "POST",
        filter_mode: str | None = None,
        fallback_username: str = "",
    ) -> list[dict[str, Any]]:
        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return []

        if len(shortcode) > 13:
            candidate = shortcode[:11]
            if shortcode_to_id(candidate) is not None:
                shortcode = candidate

        target_url = raw_target or f"{IG_BASE_URL}/p/{shortcode}/"
        has_auth = self.resilient_session.has_session_cookies()

        # 1. Mobile Media Info API (Clean TLS)
        if has_auth:
            media_id = shortcode_to_id(shortcode)
            if media_id:
                self._apply_tier_backoff(tier_attempt=1)
                info_url = f"{IG_API_BASE_URL}/media/{media_id}/info/"
                headers_mobile = self._build_headers(is_mobile=True, require_auth=True)
                res_mobile = self._make_request(
                    info_url,
                    headers=headers_mobile,
                    caller_tag="MobileMediaInfo",
                    require_auth=True,
                    fatal_429=False,
                )
                if (
                    res_mobile
                    and isinstance(res_mobile, dict)
                    and res_mobile.get("items")
                ):
                    extracted = self._extract_media_cards(
                        res_mobile["items"][0],
                        raw_target=target_url,
                        fallback_username=fallback_username,
                    )
                    if extracted:
                        valid_items: list[dict[str, Any]] = []
                        with self._lock:
                            for card in extracted:
                                is_vid = bool(card.get("is_video"))
                                if filter_mode == "reels" and not is_vid:
                                    continue
                                if filter_mode == "photos" and is_vid:
                                    continue
                                cid = str(card["id"])
                                if cid not in self.seen_ids:
                                    self.seen_ids.add(cid)
                                    self.item_found.emit(card)
                                    self.media_found.emit(card)
                                    valid_items.append(card)
                        if valid_items:
                            return valid_items

        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return []

        # 2. Captioned Embed Iframe
        embed_url = f"{IG_BASE_URL}/p/{shortcode}/embed/captioned/"
        try:
            status_code, _, _, html_text = self.resilient_session.request(
                "GET",
                embed_url,
                headers=self._build_headers(require_auth=False),
                timeout=10.0,
                require_auth=False,
            )
            if 200 <= status_code < 300 and html_text:
                card = self._extract_from_embed_html(
                    html_text,
                    shortcode,
                    raw_target=target_url,
                    fallback_username=fallback_username,
                )
                if card:
                    is_vid = bool(card.get("is_video"))
                    if filter_mode == "reels" and not is_vid:
                        return []
                    if filter_mode == "photos" and is_vid:
                        return []

                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)
                    return [card]
        except Exception as exc:
            logger.debug("Embed fallback failed for %s: %s", shortcode, exc)

        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return []

        # 3. Dedicated yt-dlp Extractor
        self._apply_tier_backoff(tier_attempt=2)
        ytdlp_cards = self._inspect_single_post_ytdlp(
            target_url,
            shortcode=shortcode,
            fallback_username=fallback_username,
            filter_mode=filter_mode,
        )
        if ytdlp_cards:
            valid_items = []
            with self._lock:
                for card in ytdlp_cards:
                    cid = str(card["id"])
                    if cid not in self.seen_ids:
                        self.seen_ids.add(cid)
                        self.item_found.emit(card)
                        self.media_found.emit(card)
                        valid_items.append(card)
            if valid_items:
                return valid_items

        return []

    def _inspect_single_post_ytdlp(
        self,
        url: str,
        shortcode: str,
        fallback_username: str = "",
        filter_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        if (
            yt_dlp is None
            or self.is_cancelled
            or self.resilient_session.is_circuit_open
        ):
            return []

        cfile = self._ensure_cookie_file()
        has_cookies = bool(cfile and os.path.exists(cfile))

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
        if self.max_items_per_profile > 0:
            ydl_opts["playlistend"] = self.max_items_per_profile

        if has_cookies and cfile:
            ydl_opts["cookiefile"] = cfile

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_obj = ydl.extract_info(url, download=False)
                if not info_obj or not isinstance(info_obj, dict):
                    return []
                info = cast(Dict[str, Any], info_obj)

                uploader_handle = ""
                for u_field in ("uploader_url", "channel_url"):
                    u_val = str(info.get(u_field) or "")
                    m = re.search(r"instagram\.com/([a-zA-Z0-9_\.]+)/?", u_val)
                    if m and m.group(1).lower() not in ("p", "reel", "reels"):
                        uploader_handle = m.group(1)
                        break
                if not uploader_handle:
                    u_cand = str(
                        info.get("uploader_id") or info.get("uploader") or ""
                    ).strip()
                    if (
                        u_cand
                        and not u_cand.isdigit()
                        and re.match(r"^[a-zA-Z0-9_\.]{3,30}$", u_cand)
                    ):
                        uploader_handle = u_cand
                uploader_handle = uploader_handle or fallback_username or "instagram"

                raw_entries = info.get("entries")
                entries: list[dict[str, Any]] = (
                    [e for e in raw_entries if isinstance(e, dict)]
                    if isinstance(raw_entries, list)
                    else []
                )

                caption = str(
                    info.get("description") or info.get("title") or ""
                ).strip()
                first_line = (
                    caption.splitlines()[0].strip()
                    if caption
                    else f"Instagram #{shortcode}"
                )
                t_idx = getattr(self, "_current_target_index", 0)

                if len(entries) > 1:
                    slides: list[dict[str, Any]] = []
                    for s_idx, entry in enumerate(entries, start=1):
                        is_vid = bool(
                            entry.get("video_ext")
                            or (entry.get("vcodec") and entry.get("vcodec") != "none")
                            or entry.get("ext") == "mp4"
                        )
                        dl_url = str(entry.get("url") or "")
                        thumb = str(entry.get("thumbnail") or "")
                        slides.append(
                            {
                                "index": s_idx,
                                "id": str(entry.get("id") or f"{shortcode}_{s_idx}"),
                                "is_video": is_vid,
                                "video_url": dl_url if is_vid else "",
                                "download_url": dl_url or thumb,
                                "thumbnail_url": thumb or dl_url,
                            }
                        )

                    primary_thumb = slides[0]["thumbnail_url"] if slides else ""
                    self._current_sub_index += 1
                    return [
                        {
                            "id": shortcode,
                            "shortcode": shortcode,
                            "title": first_line
                            or f"Instagram Carousel #{shortcode} ({len(slides)} items)",
                            "username": uploader_handle,
                            "url": url,
                            "thumbnail_url": primary_thumb,
                            "video_url": "",
                            "download_url": url,
                            "caption": caption,
                            "duration": float(info.get("duration") or 0.0),
                            "view_count": int(info.get("view_count") or 0),
                            "like_count": int(info.get("like_count") or 0),
                            "media_type": f"CAROUSEL ({len(slides)})",
                            "carousel_count": len(slides),
                            "slides": slides,
                            "is_video": any(s["is_video"] for s in slides),
                            "quality": self.quality_preset,
                            "selected": True,
                            "status": "ready",
                            "target_index": t_idx,
                            "sub_index": self._current_sub_index,
                        }
                    ]

                single_entry = entries[0] if entries else info
                has_video = bool(
                    single_entry.get("video_ext")
                    or (
                        single_entry.get("vcodec")
                        and single_entry.get("vcodec") != "none"
                    )
                    or single_entry.get("ext") == "mp4"
                    or "/reel/" in url.lower()
                )
                b_type = "REEL" if has_video else "IMAGE"
                if filter_mode == "reels" and not has_video:
                    return []
                if filter_mode == "photos" and has_video:
                    return []

                dl_url = str(single_entry.get("url") or "")
                thumb = str(single_entry.get("thumbnail") or "")
                self._current_sub_index += 1
                return [
                    {
                        "id": shortcode,
                        "shortcode": shortcode,
                        "title": first_line or f"Instagram {b_type} #{shortcode}",
                        "username": uploader_handle,
                        "url": url,
                        "thumbnail_url": thumb or dl_url,
                        "video_url": dl_url if has_video else "",
                        "download_url": dl_url or thumb or url,
                        "caption": caption,
                        "duration": float(single_entry.get("duration") or 0.0),
                        "view_count": int(single_entry.get("view_count") or 0),
                        "like_count": int(single_entry.get("like_count") or 0),
                        "media_type": b_type,
                        "is_video": has_video,
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                        "target_index": t_idx,
                        "sub_index": self._current_sub_index,
                    }
                ]
        except Exception as exc:
            logger.debug("yt-dlp single post extraction error for %s: %s", url, exc)
            return []

    def _inspect_via_ytdlp(
        self, url: str, default_username: str = "", filter_mode: str = "all"
    ) -> None:
        if (
            self.is_cancelled
            or self.resilient_session.is_circuit_open
            or yt_dlp is None
        ):
            return

        try:
            cfile = self._ensure_cookie_file()
            has_cookies = bool(cfile and os.path.exists(cfile))

            if default_username:
                clean_url = f"{IG_BASE_URL}/{default_username}/"
            else:
                raw_clean = normalize_url(url) or url
                clean_url = re.sub(r"/reels/?$", "/", raw_clean)
                if not clean_url.endswith("/"):
                    clean_url += "/"

            self.status_message.emit(f"⚙️ Running yt-dlp extraction for {clean_url}...")

            ydl_opts: Dict[str, Any] = {
                "extract_flat": "in_playlist" if has_cookies else False,
                "noplaylist": False if has_cookies else True,
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

                first_entry = entries[0]
                uploader_handle = default_username or "instagram"
                for u_field in ("uploader_url", "channel_url"):
                    u_val = str(first_entry.get(u_field) or info.get(u_field) or "")
                    m = re.search(r"instagram\.com/([a-zA-Z0-9_\.]+)/?", u_val)
                    if m and m.group(1).lower() not in ("p", "reel", "reels"):
                        uploader_handle = m.group(1)
                        break

                t_idx = getattr(self, "_current_target_index", 0)

                for idx, entry in enumerate(entries, start=1):
                    if self.is_cancelled:
                        return

                    item_code = str(entry.get("id") or f"media_{idx}")
                    entry_url = str(entry.get("webpage_url") or entry.get("url") or "")

                    has_video = bool(
                        entry.get("video_ext")
                        or (entry.get("vcodec") and entry.get("vcodec") != "none")
                        or entry.get("ext") == "mp4"
                        or "/reel/" in entry_url.lower()
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
                        else f"{IG_BASE_URL}/p/{item_code}/"
                    )

                    self._current_sub_index += 1
                    card = {
                        "id": item_code,
                        "shortcode": item_code,
                        "title": entry.get("title")
                        or f"Instagram {badge_type} #{item_code}",
                        "username": uploader_handle,
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
            logger.debug("yt-dlp extraction error: %s", ex)

    def _fetch_stories_web(
        self, username: str, user_id: str, target_story_id: Optional[str] = None
    ) -> None:
        found_any = False
        endpoints = [
            f"{IG_BASE_URL}/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"{IG_BASE_URL}/api/v1/feed/user/{user_id}/story/",
        ]

        t_idx = getattr(self, "_current_target_index", 0)

        for ep in endpoints:
            if self.is_cancelled:
                return

            headers = self._build_headers(
                referer=f"{IG_BASE_URL}/stories/{username}/",
                require_auth=True,
                is_mobile=False,
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
        if self.is_cancelled or self.resilient_session.is_circuit_open:
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

        if self.is_cancelled or self.resilient_session.is_circuit_open:
            return

        ttype = target.get("type")
        username = target.get("username") or ""
        shortcode = target.get("shortcode")
        t_idx = getattr(self, "_current_target_index", 0)

        # 1. Direct Post / Reel / Carousel
        if ttype in ("reel", "post", "carousel", "tv") and shortcode:
            self._inspect_single_post(
                shortcode,
                raw_target=raw_target,
                fallback_username=username,
            )

        # 2. Story Inspection
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
                    fatal_429=True,
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

            if self.is_cancelled or self.resilient_session.is_circuit_open:
                return

            uid = self._get_user_id(username)
            if self.is_cancelled or self.resilient_session.is_circuit_open:
                return

            if uid:
                self._fetch_stories_web(username, uid, target_story_id=shortcode)
            else:
                self._inspect_via_ytdlp(raw_target, default_username=username)

        # 3. Profile Media (Grid & Reels Tab)
        elif ttype in ("profile", "profile_reels") and username:
            effective_mode = "reels" if ttype == "profile_reels" else self.profile_mode
            uid = self._get_user_id(username)
            if self.is_cancelled or self.resilient_session.is_circuit_open:
                return

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
