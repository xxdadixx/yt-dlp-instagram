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

logger = logging.getLogger("InspectWorker")


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
            except Exception:
                pass

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

    def _build_headers(
        self,
        referer: str = "https://www.instagram.com/",
        require_auth: bool = False,
        is_mobile: bool = False,
    ) -> Dict[str, str]:
        """Construct browser-like HTTP headers with conditional cookie attachment."""
        ua = MOBILE_USER_AGENT if is_mobile else DEFAULT_USER_AGENT
        headers = {
            "User-Agent": ua,
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
            "Sec-CH-UA": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
        }
        if require_auth and self.cookie_str:
            headers["Cookie"] = self.cookie_str
            if self._csrf_token:
                headers["X-CSRFToken"] = self._csrf_token
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
        """Centralized HTTP request handler with decompression and circuit-breaker protection."""
        if self.is_cancelled:
            return None

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
                raw_bytes = resp.read()

                # Decompress Gzip / Deflate
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
                logger.debug(f"[{caller_tag or 'API'}] HTTP {e.code}: {e.reason}")
            return None
        except Exception as e:
            logger.debug(f"[{caller_tag or 'API'}] Request to {url} failed: {e}")
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

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
        """Parses raw Instagram media dictionaries into normalized MediaCard schema."""
        if not item or not isinstance(item, dict):
            return []

        media = item.get("media") or item
        if not isinstance(media, dict):
            return []

        shortcode = media.get("code") or media.get("shortcode") or ""
        user_info = media.get("user") or media.get("owner")
        if not isinstance(user_info, dict):
            user_info = {}
        username = user_info.get("username") or fallback_username

        # Safe Carousel Extraction
        sidecar_edges = []
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
            caption_text = caption_obj.get("text", "") or ""
        elif isinstance(caption_obj, str):
            caption_text = caption_obj
        elif "edge_media_to_caption" in media:
            edge_caption_obj = media.get("edge_media_to_caption")
            if isinstance(edge_caption_obj, dict):
                edges = edge_caption_obj.get("edges")
                if isinstance(edges, list) and edges and isinstance(edges[0], dict):
                    node = edges[0].get("node")
                    if isinstance(node, dict):
                        caption_text = node.get("text", "") or ""

        clean_caption = caption_text.strip()
        caption_lines = [
            line.strip() for line in clean_caption.splitlines() if line.strip()
        ]
        first_line = caption_lines[0] if caption_lines else ""

        # 1. Multi-Item Carousel Post -> Consolidate into ONE Card
        if carousel_children:
            total = len(carousel_children)
            slides = []
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
                        v_versions[0].get("url", "")
                        if isinstance(v_versions, list)
                        and v_versions
                        and isinstance(v_versions[0], dict)
                        else child.get("video_url", "")
                    )

                display_url = child.get("display_url", "")
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
                        best_res.get("src") if isinstance(best_res, dict) else ""
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
                        full_img_url = best_res.get("url") or display_url
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
                v_versions[0].get("url", "")
                if isinstance(v_versions, list)
                and v_versions
                and isinstance(v_versions[0], dict)
                else media.get("video_url", "")
            )

        display_url = media.get("display_url", "")
        disp_res = media.get("display_resources")
        img_v2 = media.get("image_versions2")

        if isinstance(disp_res, list) and disp_res:
            best_res = max(
                disp_res,
                key=lambda r: r.get("config_width", 0) if isinstance(r, dict) else 0,
            )
            full_img_url = (
                best_res.get("src") if isinstance(best_res, dict) else ""
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
                full_img_url = best_res.get("url") or display_url
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

        # 2. Single Post / Reel / Photo
        is_vid = (
            is_standalone_video(media)
            or bool(media.get("is_video"))
            or media.get("__typename") == "GraphVideo"
        )
        v_url = ""
        if is_vid:
            v_versions = media.get("video_versions") or []
            v_url = (
                v_versions[0].get("url", "")
                if v_versions
                else media.get("video_url", "")
            )

        display_url = media.get("display_url", "")
        if "display_resources" in media and media["display_resources"]:
            best_res = max(
                media["display_resources"], key=lambda r: r.get("config_width", 0)
            )
            full_img_url = best_res.get("src") or display_url
        elif "image_versions2" in media and media["image_versions2"].get("candidates"):
            best_res = max(
                media["image_versions2"]["candidates"],
                key=lambda r: (r.get("width", 0) * r.get("height", 0)),
            )
            full_img_url = best_res.get("url") or display_url
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
        """Tier 2: Paginate timeline media via GraphQL doc_id with Gaussian pacing."""
        DOC_ID_USER_FEED = "8845758582119845"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if len(self.seen_ids) >= self.max_items_per_profile:
                logger.info(
                    f"Reached safe crawl cap ({self.max_items_per_profile} items) for @{username}."
                )
                break

            variables: Dict[str, Any] = {
                "data": {
                    "count": 24,
                    "include_relationship_info": True,
                    "latest_besties_reel_media": True,
                    "latest_reel_media": True,
                },
                "id": str(user_id),
            }
            if end_cursor:
                variables["after"] = str(end_cursor)

            vars_encoded = urllib.parse.quote(json.dumps(variables))
            graphql_url = f"https://www.instagram.com/graphql/query/?doc_id={DOC_ID_USER_FEED}&variables={vars_encoded}"

            res = self._make_request(
                graphql_url,
                headers={
                    "Referer": f"{IG_BASE_URL}/{username}/",
                    "X-IG-App-ID": IG_APP_ID,
                },
                caller_tag="GraphQLTimeline",
                require_auth=bool(self.cookie_str),
            )
            if not isinstance(res, dict):
                break

            data_obj = res.get("data")
            if not isinstance(data_obj, dict):
                break

            user_data = data_obj.get("user") or data_obj.get(
                "xdt_api__v1__feed__user_timeline_graphql_connection"
            )
            if not isinstance(user_data, dict):
                break

            timeline_media = user_data.get("edge_owner_to_timeline_media") or user_data
            if not isinstance(timeline_media, dict):
                break

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
                    or node.get("__typename") == "GraphVideo"
                )

                if filter_mode == "reels" and not is_vid:
                    continue
                if filter_mode == "photos" and is_vid:
                    continue

                for card in self._extract_media_cards(node, fallback_username=username):
                    if filter_mode == "reels":
                        card["media_type"] = "REEL"
                    elif filter_mode == "photos" and is_vid:
                        continue

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
                f"✓ [GraphQL] Page {pages}: {len(self.seen_ids)} total items found..."
            )

            if has_next_page and not self.is_cancelled:
                self._apply_gaussian_pacing()

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Tier 1 -> Tier 2 -> Tier 3 deep profile media crawler."""
        tier_label = (
            "Reels"
            if filter_mode == "reels"
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Tier 1: Web Profile] Crawling {tier_label} for @{username}..."
        )

        # Tier 1: Direct Web Profile Harvest (Initial Grid + Video Archive)
        user_data = self._profile_cache.get(username)
        if not isinstance(user_data, dict):
            try:
                url_profile = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
                headers_p = {
                    "Referer": f"{IG_BASE_URL}/{username}/",
                    "X-IG-App-ID": IG_APP_ID,
                }
                res_p = self._make_request(
                    url_profile,
                    headers=headers_p,
                    caller_tag="WebProfileMedia",
                    require_auth=bool(self.cookie_str),
                )
                if isinstance(res_p, dict):
                    data_obj = res_p.get("data")
                    user_data = (
                        data_obj.get("user")
                        if isinstance(data_obj, dict)
                        else res_p.get("user")
                    )
                    if isinstance(user_data, dict):
                        self._profile_cache[username] = user_data
            except Exception as e:
                logger.debug(f"Web profile lookup failed: {e}")

        if isinstance(user_data, dict):
            # 1. Timeline Grid
            timeline_obj = user_data.get("edge_owner_to_timeline_media")
            timeline_edges = (
                timeline_obj.get("edges", []) if isinstance(timeline_obj, dict) else []
            )
            for edge in timeline_edges:
                if self.is_cancelled:
                    return
                node = edge.get("node", {}) if isinstance(edge, dict) else {}
                if not isinstance(node, dict) or not node:
                    continue

                is_vid = (
                    is_standalone_video(node)
                    or bool(node.get("is_video"))
                    or node.get("__typename") == "GraphVideo"
                )

                if filter_mode == "reels" and not is_vid:
                    continue
                if filter_mode == "photos" and is_vid:
                    continue

                for card in self._extract_media_cards(node, fallback_username=username):
                    if filter_mode == "reels":
                        card["media_type"] = "REEL"
                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            self.media_found.emit(card)

            # 2. Felix Video / Reels Archive
            if filter_mode != "photos":
                felix_obj = user_data.get("edge_felix_video_timeline")
                felix_edges = (
                    felix_obj.get("edges", []) if isinstance(felix_obj, dict) else []
                )
                for edge in felix_edges:
                    if self.is_cancelled:
                        return
                    node = edge.get("node", {}) if isinstance(edge, dict) else {}
                    if not isinstance(node, dict) or not node:
                        continue

                    for card in self._extract_media_cards(
                        node, fallback_username=username
                    ):
                        card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                                self.media_found.emit(card)

        # Tier 2: Dedicated GraphQL Reels Tab
        if filter_mode in ("reels", "all") and not self.is_cancelled:
            if len(self.seen_ids) < self.max_items_per_profile:
                self.status_message.emit(
                    f"🚀 [Tier 2: GraphQL Reels] Querying dedicated Reels connection for @{username}..."
                )
                self._fetch_reels_graphql(
                    username, user_id, max_items=self.max_items_per_profile
                )

        # Tier 3: Timeline Grid GraphQL Pagination (Photos & Carousels)
        if filter_mode != "reels" and not self.is_cancelled:
            if len(self.seen_ids) < self.max_items_per_profile:
                self.status_message.emit(
                    f"🚀 [Tier 3: GraphQL Timeline] Crawling timeline media for @{username}..."
                )
                self._fetch_timeline_graphql(username, user_id, filter_mode=filter_mode)

        # Tier 4: Unauthenticated guidance notification
        if len(self.seen_ids) == 0 and not self.cookie_str:
            self.status_message.emit(
                "💡 [Notice] 0 items found. User Reels feeds are login-gated by Instagram. Click 'Import Cookie' to enable full feed crawling."
            )

    def _inspect_via_ytdlp(
        self, url: str, default_username: str = "", filter_mode: str = "all"
    ) -> None:
        """Tier 4: yt-dlp flat extractor fallback targeting specific media subtabs."""
        if yt_dlp is None:
            msg = "yt-dlp engine is not installed or available in this Python environment."
            logger.warning(msg)
            self.status_message.emit(f"⚠️ {msg}")
            return

        try:
            if default_username:
                if filter_mode == "reels":
                    clean_url = f"{IG_BASE_URL}/{default_username}/reels/"
                else:
                    clean_url = f"{IG_BASE_URL}/{default_username}/"
            else:
                clean_url = normalize_url(url) or url

            self.status_message.emit(
                f"⚙️ [Tier 4: Engine Fallback] Running yt-dlp extraction for {clean_url}..."
            )
            ydl_opts = {
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
            cfile = self._ensure_cookie_file()
            if cfile and os.path.exists(cfile):
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
                    entry_url = entry.get("webpage_url") or entry.get("url") or ""

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
            logger.debug(f"yt-dlp fallback error: {ex}")

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
        self, username: str, user_id: str, max_items: int = 36
    ) -> int:
        """Dedicated GraphQL Reels crawler using PolarisProfileReelsTabRootQuery via POST."""
        DOC_ID_REELS_TAB = "7461877073848777"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            if len(self.seen_ids) >= max_items:
                break

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
                "doc_id": DOC_ID_REELS_TAB,
                "variables": json.dumps(variables),
                "server_timestamps": "true",
            }
            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-IG-App-ID": IG_APP_ID,
                "X-FB-Friendly-Name": "PolarisProfileReelsTabRootQuery",
                "Referer": f"{IG_BASE_URL}/{username}/reels/",
                "Origin": IG_BASE_URL,
            }

            res = self._make_request(
                "https://www.instagram.com/graphql/query",
                headers=headers,
                data=encoded_data,
                method="POST",
                caller_tag="GraphQLReelsTab",
                require_auth=bool(self.cookie_str),
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
