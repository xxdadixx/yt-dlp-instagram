"""
core/inspect_worker.py - Multi-tier background media inspection worker for Instagram.
Extracts post, reel, carousel, profile, and story metadata across 6 fallback tiers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError:

    class QThread:  # type: ignore
        def __init__(self, parent=None):
            pass

        def isRunning(self) -> bool:
            return False

        def start(self) -> None:
            self.run()

        def cancel(self) -> None:
            pass

        def wait(self, timeout=None) -> bool:
            return True

    def pyqtSignal(*args, **kwargs):  # type: ignore
        class Signal:
            def __init__(self):
                self._slots = []

            def emit(self, *a, **kw):
                for s in list(self._slots):
                    try:
                        s(*a, **kw)
                    except Exception:
                        pass

            def connect(self, slot):
                if slot not in self._slots:
                    self._slots.append(slot)

            def disconnect(self, slot=None):
                if slot is None:
                    self._slots.clear()
                elif slot in self._slots:
                    self._slots.remove(slot)

        return Signal()


try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from config.constants import (
    DEFAULT_HEADERS,
    DEFAULT_MOBILE_HEADERS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_USER_AGENT,
    IG_APP_ID,
    IG_BASE_URL,
    IG_CLIPS_USER_URL,
    IG_FEED_USER_URL,
    IG_USER_INFO_MOBILE_URL,
    IG_USER_LOOKUP_URL,
    IG_USERS_SEARCH_URL,
    IG_WEB_PROFILE_ALT_URL,
    IG_WEB_PROFILE_INFO_URL,
    IG_WEB_SEARCH_URL,
    MAX_PAGINATION_PAGES,
    MEDIA_TYPE_CAROUSEL,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    MOBILE_USER_AGENT,
    POST_REEL_REGEX,
    REELS_TAB_REGEX,
    REQUEST_DELAY_SECONDS,
    RESERVED_USERNAMES,
)
from core.parser import (
    is_standalone_video,
    parse_instagram_url,
    shortcode_to_id,
)

logger = logging.getLogger(__name__)


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
    """
    Background worker thread to inspect Instagram media URLs.
    Extracts standalone reels and video media items, populating the grid dynamically.
    """

    progress = pyqtSignal(int)
    item_found = pyqtSignal(dict)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(
        self,
        targets: List[str],
        cookie_str: Optional[str] = None,
        cookie_file: Optional[str] = None,
        quality_preset: str = "best_video",
        parent=None,
    ):
        super().__init__(parent)
        self.targets: List[str] = targets or []
        self.cookie_str: str = (cookie_str or "").strip()
        self.cookie_file: str = (cookie_file or "").strip()
        self.quality_preset: str = quality_preset
        self.is_cancelled: bool = False
        self.seen_ids: Set[str] = set()
        self._csrf_token: Optional[str] = self._extract_csrf_token()
        self._ssl_ctx = ssl._create_unverified_context()

        if not self.cookie_str and not self.cookie_file:
            try:
                from core.cookie_manager import CookieManager

                cm = CookieManager(storage_dir="config")
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
        """Gracefully flags the worker to cancel processing."""
        self.is_cancelled = True

    def _load_cookie_file(self, path: str) -> None:
        """Extracts semicolon-separated cookie header from Netscape cookies.txt file."""
        try:
            from core.cookie_manager import CookieManager

            cm = CookieManager(storage_dir=os.path.dirname(path) or "config")
            cm.import_cookie_file(path)
            self.cookie_str = cm.get_cookie_string()
            self._csrf_token = cm.get_csrf_token()
        except Exception as e:
            logger.debug(f"Failed to parse cookie file in InspectWorker: {e}")

    def _extract_csrf_token(self) -> Optional[str]:
        """Extracts csrftoken from current cookie string."""
        if not self.cookie_str:
            return None
        m = re.search(r"(?:^|;\s*|\b)csrftoken=([^;]+)", self.cookie_str)
        return m.group(1) if m else None

    def _ensure_cookie_file(self) -> Optional[str]:
        """Ensures a valid Netscape cookie file exists on disk for yt-dlp."""
        if self.cookie_file and os.path.exists(self.cookie_file):
            return self.cookie_file
        if self.cookie_str:
            try:
                from core.cookie_manager import CookieManager

                cm = CookieManager(storage_dir="config")
                cm.import_cookie_string(self.cookie_str)
                fpath = cm.get_cookie_file_path()
                if fpath and os.path.exists(fpath):
                    self.cookie_file = fpath
                    return fpath
            except Exception as e:
                logger.debug(f"Failed to create temporary cookie file for yt-dlp: {e}")
        return None

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Optional[Dict[str, Any]]:
        """Sends an HTTP request with appropriate Instagram headers and returns parsed JSON."""
        if self.is_cancelled:
            return None

        req_headers = dict(DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)
        if self.cookie_str:
            req_headers["Cookie"] = self.cookie_str
        if self._csrf_token:
            req_headers["X-CSRFToken"] = self._csrf_token

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout
            ) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                raw = resp.read().decode(charset, errors="replace")
                raw_trimmed = raw.strip()
                if raw_trimmed.startswith(("{", "[")):
                    return json.loads(raw_trimmed)
                return None
        except urllib.error.HTTPError as e:
            logger.debug(f"HTTP error {e.code} for {url}: {e.reason}")
            if e.code == 429:
                self.status_message.emit(
                    "Rate limited by Instagram (HTTP 429). Switching to fallback..."
                )
            elif e.code in (401, 403):
                self.status_message.emit(
                    "Authentication required (HTTP 401/403). Using session cookies or fallback..."
                )
            try:
                err_body = e.read().decode("utf-8", errors="replace")
                if err_body:
                    err_json = json.loads(err_body)
                    msg = err_json.get("message") or err_json.get("error_title")
                    if msg:
                        logger.debug(f"Instagram API response ({e.code}): {msg}")
            except Exception:
                pass
            return None
        except Exception as e:
            logger.debug(f"Request to {url} failed: {e}")
            return None

    def _fetch_html(
        self, url: str, timeout: int = DEFAULT_REQUEST_TIMEOUT
    ) -> Optional[str]:
        """Fetches raw HTML string from a webpage with standard browser document navigation headers."""
        if self.is_cancelled:
            return None

        req_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if self.cookie_str:
            req_headers["Cookie"] = self.cookie_str
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout
            ) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as e:
            logger.debug(f"HTML fetch for {url} failed: {e}")
            return None

    def _get_user_id(self, username: str) -> Optional[str]:
        """Resolves Instagram numeric User ID across multi-tier endpoints."""
        username = username.lower().strip().lstrip("@")

        def _extract_id_from_dict(d: Any) -> Optional[str]:
            if not d or not isinstance(d, dict):
                return None
            if "data" in d and isinstance(d["data"], dict):
                u = d["data"].get("user") or {}
                if isinstance(u, dict) and u.get("id"):
                    return str(u["id"])
            if "user" in d and isinstance(d["user"], dict):
                uid = d["user"].get("pk") or d["user"].get("id")
                if uid:
                    return str(uid)
            if "graphql" in d and isinstance(d["graphql"], dict):
                u = d["graphql"].get("user") or {}
                if isinstance(u, dict) and u.get("id"):
                    return str(u["id"])
            if "users" in d and isinstance(d["users"], list):
                for item in d["users"]:
                    u = item.get("user") or item
                    if (
                        isinstance(u, dict)
                        and str(u.get("username", "")).lower() == username
                    ):
                        uid = u.get("pk") or u.get("id")
                        if uid:
                            return str(uid)
            if "items" in d and isinstance(d["items"], list):
                for item in d["items"]:
                    if (
                        isinstance(item, dict)
                        and str(item.get("username", "")).lower() == username
                    ):
                        uid = item.get("pk") or item.get("id")
                        if uid:
                            return str(uid)
            if d.get("id") and str(d.get("id")).isdigit():
                return str(d["id"])
            if d.get("pk") and str(d.get("pk")).isdigit():
                return str(d["pk"])
            return None

        # Strategy 1: Web Profile Info
        try:
            url1 = IG_WEB_PROFILE_INFO_URL.format(username=username)
            h1 = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Referer": f"{IG_BASE_URL}/{username}/",
            }
            res1 = self._make_request(url1, headers=h1)
            uid = _extract_id_from_dict(res1)
            if uid:
                return str(uid)
        except Exception:
            pass

        if self.is_cancelled:
            return None

        # Strategy 2: Mobile Username Info
        try:
            url2 = IG_USER_INFO_MOBILE_URL.format(username=username)
            h2 = {"User-Agent": MOBILE_USER_AGENT, "X-IG-App-ID": IG_APP_ID}
            res2 = self._make_request(url2, headers=h2)
            uid = _extract_id_from_dict(res2)
            if uid:
                return str(uid)
        except Exception:
            pass

        return None

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
        """Extracts individual media cards from Instagram API/GraphQL objects (including carousels)."""
        if not item or not isinstance(item, dict):
            return []

        media = item.get("media", item)
        shortcode = media.get("code") or media.get("shortcode") or ""
        user_info = media.get("user") or media.get("owner") or {}
        username = user_info.get("username") or fallback_username

        # Check for carousel children
        carousel_children = media.get("carousel_media") or [
            edge.get("node", {})
            for edge in media.get("edge_sidecar_to_children", {}).get("edges", [])
        ]

        cards = []
        if carousel_children:
            total = len(carousel_children)
            for idx, child in enumerate(carousel_children, start=1):
                child_id = str(child.get("id") or f"{shortcode}_{idx}")
                is_vid = bool(
                    child.get("is_video")
                    or child.get("media_type") == 2
                    or child.get("video_versions")
                )

                # Video URL
                v_url = ""
                if is_vid:
                    v_versions = child.get("video_versions") or []
                    v_url = (
                        v_versions[0].get("url", "")
                        if v_versions
                        else child.get("video_url", "")
                    )

                # Image / Thumbnail URL
                img_versions = child.get("image_versions2", {}).get(
                    "candidates", []
                ) or child.get("display_resources", [])
                t_url = (
                    img_versions[0].get("url", "")
                    if img_versions
                    else child.get("display_url", "")
                )

                card_type = "CAROUSEL (VIDEO)" if is_vid else "CAROUSEL (IMAGE)"
                cards.append(
                    {
                        "id": child_id,
                        "shortcode": shortcode,
                        "title": f"Instagram Post #{shortcode} (Slide {idx}/{total})",
                        "username": username,
                        "url": f"https://www.instagram.com/p/{shortcode}/?img_index={idx}",
                        "thumbnail_url": t_url,
                        "video_url": v_url,
                        "download_url": v_url or t_url,
                        "caption": (
                            media.get("caption", {}).get("text", "")
                            if isinstance(media.get("caption"), dict)
                            else ""
                        ),
                        "duration": float(child.get("video_duration") or 0.0),
                        "view_count": int(
                            media.get("view_count") or media.get("play_count") or 0
                        ),
                        "like_count": int(media.get("like_count") or 0),
                        "media_type": card_type,
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                )
        else:
            is_vid = is_standalone_video(media)
            v_url = ""
            if is_vid:
                v_versions = media.get("video_versions") or []
                v_url = (
                    v_versions[0].get("url", "")
                    if v_versions
                    else media.get("video_url", "")
                )

            img_versions = media.get("image_versions2", {}).get(
                "candidates", []
            ) or media.get("display_resources", [])
            t_url = (
                img_versions[0].get("url", "")
                if img_versions
                else media.get("display_url", "")
            )

            b_type = (
                "REEL"
                if (
                    "/reel/" in raw_target.lower()
                    or media.get("product_type") == "clips"
                )
                else ("VIDEO" if is_vid else "IMAGE")
            )
            cards.append(
                {
                    "id": str(media.get("id") or shortcode),
                    "shortcode": shortcode,
                    "title": f"Instagram {b_type} #{shortcode}",
                    "username": username,
                    "url": raw_target or f"https://www.instagram.com/p/{shortcode}/",
                    "thumbnail_url": t_url,
                    "video_url": v_url,
                    "download_url": v_url or t_url,
                    "caption": (
                        media.get("caption", {}).get("text", "")
                        if isinstance(media.get("caption"), dict)
                        else ""
                    ),
                    "duration": float(media.get("video_duration") or 0.0),
                    "view_count": int(
                        media.get("view_count") or media.get("play_count") or 0
                    ),
                    "like_count": int(media.get("like_count") or 0),
                    "media_type": b_type,
                    "quality": self.quality_preset,
                    "selected": True,
                    "status": "ready",
                }
            )

        return cards

    def _fetch_stories_web(self, username: str, user_id: str) -> None:
        """Fetches active user stories using Instagram Story Reel API with yt-dlp fallback."""
        self.status_message.emit(f"Inspecting active stories for @{username}...")
        found_any = False

        story_endpoints = [
            f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/story/",
        ]

        h = {
            "User-Agent": MOBILE_USER_AGENT,
            "X-IG-App-ID": IG_APP_ID,
        }
        if self._csrf_token:
            h["X-CSRFToken"] = self._csrf_token

        for endpoint in story_endpoints:
            if self.is_cancelled:
                return
            res = self._make_request(endpoint, headers=h)
            if not res or not isinstance(res, dict):
                continue

            items = []
            if "reels" in res and isinstance(res["reels"], dict):
                user_reel = res["reels"].get(str(user_id)) or {}
                items = user_reel.get("items", [])
            elif (
                "reels_media" in res
                and isinstance(res["reels_media"], list)
                and res["reels_media"]
            ):
                items = res["reels_media"][0].get("items", [])
            elif "reel" in res and isinstance(res["reel"], dict):
                items = res["reel"].get("items", [])
            elif "items" in res and isinstance(res["items"], list):
                items = res["items"]

            if items:
                total_stories = len(items)
                for idx, item in enumerate(items, start=1):
                    if self.is_cancelled:
                        return
                    cards = self._extract_media_cards(item, fallback_username=username)
                    for card in cards:
                        cid = card["id"]
                        card["media_type"] = "STORY"
                        card["title"] = f"@{username} Story ({idx}/{total_stories})"
                        card["url"] = f"{IG_BASE_URL}/stories/{username}/{cid}/"
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            found_any = True
                if found_any:
                    return

        if not found_any and not self.is_cancelled:
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/stories/{username}/", default_username=username
            )

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, reels_only: bool = False
    ) -> None:
        """Multi-tier profile media crawler engine."""
        initial_count = len(self.seen_ids)
        tier_label = "Reels" if reels_only else "Profile Media"
        self.status_message.emit(f"Inspecting {tier_label} for @{username}...")

        # 1. Clips API Pagination
        if reels_only or not self.is_cancelled:
            max_id: Optional[str] = None
            tier1_pages = 0
            while tier1_pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
                payload: Dict[str, str] = {
                    "target_user_id": str(user_id),
                    "page_size": str(DEFAULT_PAGE_SIZE),
                    "include_feed_video": "true",
                    "container_module": "clips_viewer_user",
                }
                if max_id:
                    payload["max_id"] = str(max_id)

                encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
                h = {
                    "User-Agent": MOBILE_USER_AGENT,
                    "X-IG-App-ID": IG_APP_ID,
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                if self._csrf_token:
                    h["X-CSRFToken"] = self._csrf_token

                res = self._make_request(
                    IG_CLIPS_USER_URL,
                    headers=h,
                    data=encoded_data,
                    method="POST",
                )
                if not res or not isinstance(res, dict):
                    params_str = urllib.parse.urlencode(payload)
                    res = self._make_request(
                        f"{IG_CLIPS_USER_URL}?{params_str}",
                        headers=h,
                    )

                if not res or not isinstance(res, dict):
                    break

                items = res.get("items") or res.get("grid_items") or []
                if not items:
                    break

                for item in items:
                    if self.is_cancelled:
                        return
                    if reels_only and not is_standalone_video(item):
                        continue

                    cards = self._extract_media_cards(item, fallback_username=username)
                    for card in cards:
                        cid = card["id"]
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)

                tier1_pages += 1
                self.status_message.emit(
                    f"@{username}: Found {len(self.seen_ids)} items (Clips Page {tier1_pages})..."
                )

                paging_info = res.get("paging_info", {})
                more_available = (
                    paging_info.get("more_available")
                    if "more_available" in paging_info
                    else res.get("more_available", False)
                )
                next_max_id = (
                    paging_info.get("max_id")
                    or paging_info.get("next_max_id")
                    or res.get("next_max_id")
                    or res.get("max_id")
                )

                if not more_available or not next_max_id or next_max_id == max_id:
                    break
                max_id = next_max_id

                for _ in range(int(REQUEST_DELAY_SECONDS * 10)):
                    if self.is_cancelled:
                        return
                    time.sleep(0.1)

        # 2. Timeline Feed Pagination
        if not self.is_cancelled:
            self.status_message.emit(f"Checking timeline feed for @{username}...")
            feed_max_id: Optional[str] = None
            tier2_pages = 0
            while tier2_pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
                feed_url = IG_FEED_USER_URL.format(user_id=user_id)
                if feed_max_id:
                    feed_url += f"?max_id={feed_max_id}"

                h_feed = {
                    "User-Agent": MOBILE_USER_AGENT,
                    "X-IG-App-ID": IG_APP_ID,
                }
                if self._csrf_token:
                    h_feed["X-CSRFToken"] = self._csrf_token

                res_feed = self._make_request(feed_url, headers=h_feed)
                if not res_feed or not isinstance(res_feed, dict):
                    break

                items_feed = res_feed.get("items", [])
                if not items_feed:
                    break

                for item in items_feed:
                    if self.is_cancelled:
                        return
                    if reels_only and not is_standalone_video(item):
                        continue

                    cards = self._extract_media_cards(item, fallback_username=username)
                    for card in cards:
                        cid = card["id"]
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)

                tier2_pages += 1
                more_available = res_feed.get("more_available", False)
                next_max_id = res_feed.get("next_max_id") or res_feed.get("max_id")

                if not more_available or not next_max_id or next_max_id == feed_max_id:
                    break
                feed_max_id = next_max_id

                for _ in range(int(REQUEST_DELAY_SECONDS * 10)):
                    if self.is_cancelled:
                        return
                    time.sleep(0.1)

        # 3. yt-dlp Fallback if 0 items found
        if len(self.seen_ids) == initial_count and not self.is_cancelled:
            self.status_message.emit(
                f"Scraping media via yt-dlp fallback for @{username}..."
            )
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/{username}/", default_username=username
            )

    def _fetch_all_reels_web(self, username: str, user_id: str) -> None:
        """Reels tab inspection helper."""
        self._fetch_all_profile_media_web(username, user_id, reels_only=True)

    def _extract_embed_cards(
        self, html: str, shortcode: str, raw_target: str = ""
    ) -> List[Dict[str, Any]]:
        """Extracts media cards from public Instagram Embed captioned HTML."""
        if not html:
            return []

        video_url = ""
        for pat in [
            r'"video_url"\s*:\s*"([^"]+)"',
            r'<video[^>]+src="([^"]+)"',
            r'class="EmbeddedVideo"[^>]*src="([^"]+)"',
            r'src="([^"]+\.mp4[^"]*)"',
            r'"playback_url"\s*:\s*"([^"]+)"',
        ]:
            v_match = re.search(pat, html, re.IGNORECASE)
            if v_match:
                video_url = (
                    v_match.group(1)
                    .replace("&amp;", "&")
                    .replace("\\/", "/")
                    .replace("\\u0026", "&")
                )
                break

        thumb_url = ""
        for pat in [
            r'"display_url"\s*:\s*"([^"]+)"',
            r'"thumbnail_src"\s*:\s*"([^"]+)"',
            r'<img[^>]+class="[^"]*EmbeddedMedia[^"]*"[^>]+src="([^"]+)"',
            r'<img[^>]+src="([^"]+)"[^>]+class="[^"]*EmbeddedMedia[^"]*"',
            r'<img[^>]+class="[^"]*Image[^"]*"[^>]+src="([^"]+)"',
            r'<img[^>]+src="([^"]+)"',
        ]:
            t_match = re.search(pat, html, re.IGNORECASE)
            if t_match:
                cand = (
                    t_match.group(1)
                    .replace("&amp;", "&")
                    .replace("\\/", "/")
                    .replace("\\u0026", "&")
                )
                if not cand.startswith("data:"):
                    thumb_url = cand
                    break

        username = ""
        for pat in [
            r'"username"\s*:\s*"([^"]+)"',
            r'class="UsernameText"[^>]*>([^<]+)<',
            r'class="[^"]*Username[^"]*"[^>]*>([^<]+)<',
            r'href="\/([a-zA-Z0-9_.]+)\/"[^>]*class="[^"]*Username',
        ]:
            u_match = re.search(pat, html, re.IGNORECASE)
            if u_match:
                username = u_match.group(1).strip()
                break

        caption = ""
        cap_match = re.search(r'class="Caption"[^>]*>(.*?)<\/div>', html, re.DOTALL)
        if cap_match:
            caption = re.sub(r"<[^>]+>", "", cap_match.group(1)).strip()

        if not thumb_url and not video_url and not caption:
            return []

        badge_type = "reel" if video_url else "post"
        canonical_url = raw_target or (
            f"{IG_BASE_URL}/reel/{shortcode}/"
            if badge_type == "reel"
            else f"{IG_BASE_URL}/p/{shortcode}/"
        )

        card = {
            "id": shortcode,
            "shortcode": shortcode,
            "title": (
                caption.splitlines()[0][:60]
                if caption.splitlines()
                else f"Instagram {badge_type.capitalize()} {shortcode}"
            ),
            "username": username,
            "url": canonical_url,
            "thumbnail_url": thumb_url,
            "video_url": video_url,
            "download_url": video_url or thumb_url,
            "caption": caption,
            "duration": 0.0,
            "view_count": 0,
            "like_count": 0,
            "media_type": badge_type,
            "quality": self.quality_preset,
            "selected": True,
            "status": "ready",
        }
        return [card]

    def _extract_opengraph_cards(
        self, html: str, shortcode: str, raw_target: str = ""
    ) -> List[Dict[str, Any]]:
        """Extracts OpenGraph and JSON-LD metadata from public post or reel HTML."""
        if not html:
            return []

        def _get_meta(prop: str) -> str:
            m = re.search(
                rf'<meta\s+(?:property|name)="{prop}"\s+content="([^"]+)"',
                html,
                re.IGNORECASE,
            )
            if not m:
                m = re.search(
                    rf'<meta\s+content="([^"]+)"\s+(?:property|name)="{prop}"',
                    html,
                    re.IGNORECASE,
                )
            return (
                m.group(1)
                .replace("&amp;", "&")
                .replace("\\/", "/")
                .replace("\\u0026", "&")
                if m
                else ""
            )

        video_url = (
            _get_meta("og:video")
            or _get_meta("og:video:secure_url")
            or _get_meta("twitter:player:stream")
        )
        thumb_url = (
            _get_meta("og:image")
            or _get_meta("og:image:secure_url")
            or _get_meta("twitter:image")
        )
        title = _get_meta("og:title")
        desc = _get_meta("og:description")

        if not video_url:
            vm = re.search(r'"video_url"\s*:\s*"([^"]+)"', html)
            if vm:
                video_url = (
                    vm.group(1)
                    .replace("&amp;", "&")
                    .replace("\\/", "/")
                    .replace("\\u0026", "&")
                )
        if not thumb_url:
            tm = re.search(r'"display_url"\s*:\s*"([^"]+)"', html)
            if tm:
                thumb_url = (
                    tm.group(1)
                    .replace("&amp;", "&")
                    .replace("\\/", "/")
                    .replace("\\u0026", "&")
                )

        jsonld_match = re.search(
            r"<script[^>]*type=['\"]application\/ld\+json['\"][^>]*>(.+?)<\/script>",
            html,
            re.DOTALL,
        )
        if jsonld_match:
            try:
                ld = json.loads(jsonld_match.group(1))
                if isinstance(ld, dict):
                    if not video_url and ld.get("video"):
                        v = ld["video"]
                        video_url = (
                            v.get("contentUrl", "") if isinstance(v, dict) else str(v)
                        )
                    if not thumb_url and ld.get("image"):
                        img = ld["image"]
                        thumb_url = (
                            img.get("url", "") if isinstance(img, dict) else str(img)
                        )
                    if not desc and ld.get("caption"):
                        desc = ld.get("caption", "")
            except Exception:
                pass

        if not thumb_url and not video_url and not title:
            return []

        badge_type = (
            "reel" if video_url or "/reel/" in (raw_target or "").lower() else "post"
        )
        canonical_url = raw_target or (
            f"{IG_BASE_URL}/reel/{shortcode}/"
            if badge_type == "reel"
            else f"{IG_BASE_URL}/p/{shortcode}/"
        )

        card = {
            "id": shortcode,
            "shortcode": shortcode,
            "title": title
            or (
                desc.splitlines()[0][:60]
                if desc
                else f"Instagram {badge_type.capitalize()} {shortcode}"
            ),
            "username": "",
            "url": canonical_url,
            "thumbnail_url": thumb_url,
            "video_url": video_url,
            "download_url": video_url or thumb_url,
            "caption": desc or title,
            "duration": 0.0,
            "view_count": 0,
            "like_count": 0,
            "media_type": badge_type,
            "quality": self.quality_preset,
            "selected": True,
            "status": "ready",
        }
        return [card]

    def _inspect_via_ytdlp(self, url: str, default_username: str = "") -> None:
        """Fallback extraction using yt-dlp engine with clean URL passing and error resilience."""
        if yt_dlp is None:
            self.error.emit("yt-dlp engine is not installed or available.")
            return

        try:
            is_single = any(
                x in url.lower() for x in ("/p/", "/reel/", "/reels/", "/tv/", "/r/")
            )
            ydl_opts = {
                "extract_flat": False,
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

            clean_url = normalize_url(url) or url
            if REELS_TAB_REGEX.match(clean_url):
                clean_url = re.sub(r"/reels/?.*$", "/", clean_url)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(clean_url, download=False)
                except Exception as ex:
                    logger.debug(f"yt-dlp extract_info error: {ex}")
                    info = None

                if not info or not isinstance(info, dict):
                    return

                entries = info.get("entries")
                if entries is not None:
                    try:
                        entries = [e for e in entries if e]
                    except Exception:
                        entries = [info]
                else:
                    entries = [info]

                if not entries:
                    entries = [info]

                total_entries = len(entries)
                target_entries = list(enumerate(entries, start=1))

                for idx, entry in target_entries:
                    if self.is_cancelled:
                        return
                    if not entry or not isinstance(entry, dict):
                        continue

                    code = str(
                        entry.get("id")
                        or entry.get("url", "").rstrip("/").split("/")[-1]
                        or f"media_{idx}"
                    )
                    base_code = code.split("_")[0]
                    uploader = (
                        entry.get("uploader")
                        or entry.get("channel")
                        or info.get("uploader")
                        or default_username
                    )

                    formats = entry.get("formats", [])
                    has_video = (
                        bool(entry.get("video_ext"))
                        or bool(entry.get("vcodec") and entry.get("vcodec") != "none")
                        or any(
                            isinstance(f, dict) and f.get("vcodec") != "none"
                            for f in formats
                        )
                        or ("/reel/" in url.lower() or "/r/" in url.lower())
                        or (entry.get("ext") == "mp4")
                    )

                    is_story = (
                        "/stories/" in url.lower() and "/highlights/" not in url.lower()
                    )
                    is_highlight = (
                        "/stories/highlights/" in url.lower() or "/s/" in url.lower()
                    )
                    is_audio = "/audio/" in url.lower() or entry.get("ext") in (
                        "mp3",
                        "m4a",
                        "wav",
                    )
                    is_reel = (
                        "/reel/" in url.lower()
                        or "/reels/" in url.lower()
                        or "/r/" in url.lower()
                    )

                    if is_story:
                        badge_media_type = "STORY"
                    elif is_highlight:
                        badge_media_type = "HIGHLIGHT"
                    elif is_audio:
                        badge_media_type = "AUDIO"
                    elif is_reel:
                        badge_media_type = "REEL"
                    elif total_entries > 1:
                        badge_media_type = (
                            "CAROUSEL (VIDEO)" if has_video else "CAROUSEL (IMAGE)"
                        )
                    else:
                        badge_media_type = "VIDEO" if has_video else "IMAGE"

                    direct_url = ""
                    entry_raw_url = entry.get("url")
                    if isinstance(entry_raw_url, str) and entry_raw_url.startswith(
                        "http"
                    ):
                        is_webpage = any(
                            entry_raw_url.startswith(f"https://{d}/")
                            or entry_raw_url.startswith(f"http://{d}/")
                            for d in (
                                "instagram.com",
                                "www.instagram.com",
                                "instagr.am",
                                "ddinstagram.com",
                                "kkinstagram.com",
                                "ig.me",
                            )
                        )
                        if not is_webpage:
                            direct_url = entry_raw_url

                    if formats and isinstance(formats, list):
                        valid_formats = [
                            f
                            for f in formats
                            if isinstance(f, dict)
                            and f.get("url")
                            and not any(
                                d in f["url"]
                                for d in ("instagram.com/p/", "instagram.com/reel/")
                            )
                        ]
                        if valid_formats:
                            if has_video:
                                best_fmt = max(
                                    valid_formats,
                                    key=lambda f: (
                                        f.get("height") or 0,
                                        f.get("tbr") or 0,
                                    ),
                                )
                                direct_url = best_fmt.get("url") or direct_url
                            elif not direct_url:
                                best_fmt = max(
                                    valid_formats,
                                    key=lambda f: (
                                        f.get("height") or 0,
                                        f.get("width") or 0,
                                    ),
                                )
                                direct_url = best_fmt.get("url") or direct_url

                    thumb_url = entry.get("thumbnail")
                    if not thumb_url and entry.get("thumbnails"):
                        thumb_url = entry["thumbnails"][-1].get("url")
                    if not thumb_url and not has_video:
                        thumb_url = direct_url

                    title_str = (
                        entry.get("title")
                        or entry.get("description")
                        or f"Instagram {badge_media_type} {code}"
                    )
                    title_first_line = (
                        title_str.splitlines()[0]
                        if title_str
                        else f"Instagram {badge_media_type} {code}"
                    )

                    card_id = (
                        f"{base_code}_{idx}"
                        if (total_entries > 1 and not code.endswith(f"_{idx}"))
                        else code
                    )
                    if card_id in self.seen_ids:
                        continue

                    if is_story:
                        slide_title = (
                            f"@{uploader} Story ({idx}/{total_entries})"
                            if total_entries > 1
                            else f"@{uploader} Story"
                        )
                        item_url = (
                            entry.get("webpage_url")
                            or f"{IG_BASE_URL}/stories/{uploader}/{base_code}/"
                        )
                    elif is_highlight:
                        slide_title = (
                            f"Story Highlight ({idx}/{total_entries})"
                            if total_entries > 1
                            else "Story Highlight"
                        )
                        item_url = entry.get("webpage_url") or url
                    elif total_entries > 1:
                        slide_title = (
                            f"{title_first_line} (Slide {idx}/{total_entries})"
                        )
                        item_url = f"{IG_BASE_URL}/p/{base_code}/?img_index={idx}"
                    else:
                        slide_title = title_first_line
                        item_url = entry.get("webpage_url") or (
                            f"{IG_BASE_URL}/reel/{base_code}/"
                            if badge_media_type == "REEL"
                            else f"{IG_BASE_URL}/p/{base_code}/"
                        )

                    self.seen_ids.add(card_id)
                    card = {
                        "id": card_id,
                        "shortcode": base_code,
                        "title": slide_title,
                        "username": uploader,
                        "url": item_url,
                        "thumbnail_url": thumb_url or "",
                        "video_url": direct_url if has_video else "",
                        "download_url": direct_url or thumb_url or "",
                        "caption": entry.get("description") or entry.get("title") or "",
                        "duration": float(entry.get("duration") or 0.0),
                        "view_count": int(entry.get("view_count") or 0),
                        "like_count": int(entry.get("like_count") or 0),
                        "media_type": badge_media_type,
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    self.item_found.emit(card)

        except Exception as ex:
            self.error.emit(f"yt-dlp error: {str(ex)}")

    def _inspect_single_post(self, shortcode: str, raw_target: str = "") -> None:
        """Resolves a single Instagram post, reel, carousel, or TV video across 6 fallback tiers."""
        canonical_post_url = f"{IG_BASE_URL}/p/{shortcode}/"
        canonical_reel_url = f"{IG_BASE_URL}/reel/{shortcode}/"
        web_headers = {
            "Referer": canonical_post_url,
            "Origin": "https://www.instagram.com",
            "X-IG-App-ID": IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
        }
        var_payload = urllib.parse.quote(json.dumps({"shortcode": shortcode}))

        # 1. Web GraphQL and Web JSON API endpoints
        endpoints = [
            f"https://www.instagram.com/graphql/query/?doc_id=8845758582119845&variables={var_payload}",
            f"https://www.instagram.com/graphql/query/?doc_id=10015901848480474&variables={var_payload}",
            f"https://www.instagram.com/graphql/query/?query_hash=b3055c01b4b222b8a47dc12b090e4e64&variables={var_payload}",
            f"{IG_BASE_URL}/p/{shortcode}/?__a=1&__d=dis",
            f"{IG_BASE_URL}/reel/{shortcode}/?__a=1&__d=dis",
        ]
        for endpoint in endpoints:
            if self.is_cancelled:
                return
            res = self._make_request(endpoint, headers=web_headers)
            if res and isinstance(res, dict):
                media_item = (
                    res.get("graphql", {}).get("shortcode_media")
                    or res.get("data", {}).get("xdt_shortcode_media")
                    or res.get("data", {}).get("shortcode_media")
                    or (res.get("items", [])[0] if res.get("items") else None)
                    or res.get("media")
                )
                if media_item and isinstance(media_item, dict):
                    cards = self._extract_media_cards(media_item, raw_target=raw_target)
                    if cards:
                        for card in cards:
                            cid = card["id"]
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)
                        return

        # 2. Mobile media info API
        try:
            media_id = shortcode_to_id(shortcode)
            if media_id:
                url_m = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
                h_m = {
                    "User-Agent": MOBILE_USER_AGENT,
                    "X-IG-App-ID": IG_APP_ID,
                }
                res_m = self._make_request(url_m, headers=h_m)
                if res_m and isinstance(res_m, dict):
                    items = res_m.get("items", []) or (
                        [res_m["media"]] if "media" in res_m else []
                    )
                    if items:
                        cards = self._extract_media_cards(
                            items[0], raw_target=raw_target
                        )
                        if cards:
                            for card in cards:
                                cid = card["id"]
                                if cid not in self.seen_ids:
                                    self.seen_ids.add(cid)
                                    self.item_found.emit(card)
                            return
        except Exception:
            pass

        # 3. Public Embed HTML scraper
        for embed_url in (
            f"{IG_BASE_URL}/p/{shortcode}/embed/captioned/",
            f"{IG_BASE_URL}/reel/{shortcode}/embed/captioned/",
        ):
            if self.is_cancelled:
                return
            embed_html = self._fetch_html(embed_url)
            if embed_html:
                cards = self._extract_embed_cards(
                    embed_html, shortcode, raw_target=raw_target
                )
                if cards:
                    for card in cards:
                        cid = card["id"]
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                    return

        # 4. Public oEmbed API fallback
        clean_item_url = f"{IG_BASE_URL}/p/{shortcode}/"
        for oembed_url in (
            f"https://www.instagram.com/api/v1/oembed/?url={clean_item_url}",
            f"https://api.instagram.com/oembed/?url={clean_item_url}",
        ):
            if self.is_cancelled:
                return
            res_oembed = self._make_request(oembed_url, headers=web_headers)
            if res_oembed and isinstance(res_oembed, dict):
                title = res_oembed.get("title", "")
                username = res_oembed.get("author_name", "")
                thumb = res_oembed.get("thumbnail_url", "")
                if thumb or title:
                    card = {
                        "id": shortcode,
                        "shortcode": shortcode,
                        "title": (
                            title.splitlines()[0][:60]
                            if title
                            else f"Instagram Media {shortcode}"
                        ),
                        "username": username,
                        "url": raw_target or clean_item_url,
                        "thumbnail_url": thumb,
                        "video_url": "",
                        "download_url": thumb,
                        "caption": title,
                        "duration": 0.0,
                        "view_count": 0,
                        "like_count": 0,
                        "media_type": "post",
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    if shortcode not in self.seen_ids:
                        self.seen_ids.add(shortcode)
                        self.item_found.emit(card)
                        return

        # 5. Direct Webpage OpenGraph & JSON-LD scraper
        page_html = self._fetch_html(canonical_post_url)
        if not page_html:
            page_html = self._fetch_html(canonical_reel_url)
        if page_html:
            cards = self._extract_opengraph_cards(
                page_html, shortcode, raw_target=raw_target
            )
            if cards:
                for card in cards:
                    cid = card["id"]
                    if cid not in self.seen_ids:
                        self.seen_ids.add(cid)
                        self.item_found.emit(card)
                return

        # 6. Fallback to yt-dlp engine with canonical URL
        target_url = (
            canonical_reel_url
            if (
                "/reel/" in (raw_target or "").lower()
                or "/r/" in (raw_target or "").lower()
            )
            else canonical_post_url
        )
        self._inspect_via_ytdlp(target_url)

    def run(self) -> None:
        """Worker execution loop over all input targets."""
        try:
            total_targets = len(self.targets)
            if total_targets == 0:
                self.finished.emit(0)
                return

            self.progress.emit(5)
            for idx, raw_target in enumerate(self.targets):
                if self.is_cancelled:
                    break

                target = parse_instagram_url(raw_target)
                ttype = target.get("type")
                username = target.get("username")
                shortcode = target.get("shortcode")

                if ttype == "direct_media":
                    cid = (
                        re.sub(r"[^\w\-]", "_", raw_target.split("?")[0].split("/")[-1])
                        or f"cdn_media_{idx}"
                    )
                    card = {
                        "id": cid,
                        "shortcode": cid,
                        "title": f"Instagram Direct Media ({cid})",
                        "username": "instagram",
                        "url": raw_target,
                        "thumbnail_url": (
                            raw_target
                            if any(
                                x in raw_target.lower()
                                for x in (".jpg", ".jpeg", ".png", ".webp")
                            )
                            else ""
                        ),
                        "video_url": (
                            raw_target
                            if any(
                                x in raw_target.lower()
                                for x in (".mp4", ".mov", ".m4v")
                            )
                            else ""
                        ),
                        "download_url": raw_target,
                        "caption": f"Direct CDN Stream: {raw_target}",
                        "duration": 0.0,
                        "view_count": 0,
                        "like_count": 0,
                        "media_type": (
                            "reel"
                            if any(
                                x in raw_target.lower()
                                for x in (".mp4", ".mov", ".m4v")
                            )
                            else "post"
                        ),
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    if cid not in self.seen_ids:
                        self.seen_ids.add(cid)
                        self.item_found.emit(card)

                elif ttype in ("reel", "post", "carousel", "tv") and shortcode:
                    self._inspect_single_post(shortcode, raw_target=raw_target)

                elif ttype == "profile_reels" and username:
                    user_id = self._get_user_id(username)
                    if user_id:
                        self._fetch_all_reels_web(username, user_id)
                    else:
                        self.status_message.emit(
                            f"Could not resolve ID for @{username}. Trying yt-dlp fallback..."
                        )
                        self._inspect_via_ytdlp(
                            f"{IG_BASE_URL}/{username}/", default_username=username
                        )

                elif ttype == "profile" and username:
                    user_id = self._get_user_id(username)
                    if user_id:
                        self._fetch_all_profile_media_web(
                            username, user_id, reels_only=False
                        )
                    else:
                        self._inspect_via_ytdlp(
                            f"{IG_BASE_URL}/{username}/", default_username=username
                        )

                elif ttype == "highlight":
                    self._inspect_via_ytdlp(raw_target)

                elif ttype == "story" and username:
                    user_id = self._get_user_id(username)
                    if user_id:
                        self._fetch_stories_web(username, user_id)
                    else:
                        self._inspect_via_ytdlp(raw_target, default_username=username)

                elif ttype == "audio":
                    self.status_message.emit(f"Inspecting Audio track: {raw_target}")
                    self._inspect_via_ytdlp(raw_target)

                else:
                    self._inspect_via_ytdlp(raw_target)

                pct = int(10 + (idx + 1) / total_targets * 80)
                self.progress.emit(pct)

            if not self.is_cancelled:
                self.progress.emit(100)
                if len(self.seen_ids) == 0:
                    self.status_message.emit(
                        "Inspection completed: 0 items found. (Instagram may require active session cookies)."
                    )
                else:
                    self.status_message.emit(
                        f"Inspection completed! Found {len(self.seen_ids)} items ready."
                    )
            self.finished.emit(len(self.seen_ids))

        except Exception as e:
            self.error.emit(f"Inspection error: {str(e)}")
            self.finished.emit(len(self.seen_ids))
