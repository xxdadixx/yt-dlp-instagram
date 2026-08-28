"""
core/inspect_worker.py - Multi-tier Instagram inspection worker with resilient pagination.
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

    def pyqtSignal(*args, **kwargs):  # type: ignore
        class Signal:
            def __init__(self):
                self._slots = []

            def emit(self, *a, **kw):
                for s in self._slots:
                    try:
                        s(*a, **kw)
                    except Exception:
                        pass

            def connect(self, slot):
                self._slots.append(slot)

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
    IG_WEB_PROFILE_INFO_URL,
    MAX_PAGINATION_PAGES,
    MEDIA_TYPE_CAROUSEL,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    MOBILE_USER_AGENT,
    REQUEST_DELAY_SECONDS,
)
from core.parser import (
    id_to_shortcode,
    is_standalone_video,
    normalize_url,
    parse_instagram_url,
    shortcode_to_id,
)

logger = logging.getLogger(__name__)


class InspectWorker(QThread):
    """
    Background worker thread that inspects and resolves Instagram media URLs,
    supporting multi-tier reels extraction, single posts, carousels, stories, and highlights.
    """

    item_found = pyqtSignal(dict)
    progress = pyqtSignal(int)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(
        self,
        targets: List[str],
        cookie_str: Optional[str] = None,
        cookie_file: Optional[str] = None,
        quality_preset: str = "best_video",
        download_scope: str = "all",
        parent=None,
    ):
        super().__init__(parent)
        self.targets: List[str] = targets or []
        self.cookie_str: str = (cookie_str or "").strip()
        self.cookie_file: str = (cookie_file or "").strip()
        self.quality_preset: str = quality_preset
        self.download_scope: str = download_scope
        self.is_cancelled: bool = False
        self.seen_ids: Set[str] = set()

        if (
            not self.cookie_str
            and self.cookie_file
            and os.path.exists(self.cookie_file)
        ):
            self.cookie_str = self._load_cookie_file(self.cookie_file)

        self._csrf_token: Optional[str] = self._extract_csrf_token(self.cookie_str)
        self._ssl_ctx = ssl._create_unverified_context()

    def cancel(self) -> None:
        """Gracefully flags the worker to stop processing."""
        self.is_cancelled = True

    def _load_cookie_file(self, file_path: str) -> str:
        """Loads Netscape, JSON, or plain cookie file into standard header string."""
        try:
            from core.cookie_manager import CookieManager

            cm = CookieManager()
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
            header_str, _ = cm._parse_cookie_data(raw_text)
            return header_str if header_str else raw_text.strip()
        except Exception:
            return ""

    def _extract_csrf_token(self, cookie_str: str) -> Optional[str]:
        """Extracts the csrftoken value from a cookie string or cookie file."""
        if not cookie_str:
            return None
        match = re.search(r"(?:^|;\s*|\b)csrftoken=([^;]+)", cookie_str)
        return match.group(1) if match else None

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
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"Request to {url} failed: {e}")
            return None

    def _fetch_html(
        self, url: str, timeout: int = DEFAULT_REQUEST_TIMEOUT
    ) -> Optional[str]:
        """Fetches raw HTML string from a webpage."""
        if self.is_cancelled:
            return None

        req_headers = dict(DEFAULT_HEADERS)
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
        """
        Resolves Instagram numeric User ID using 4 fallback strategies:
        1. Web Profile Info endpoint (web_profile_info)
        2. Mobile Username Info endpoint & user lookup
        3. Direct HTML Regex scraping
        4. yt-dlp metadata extraction
        """
        username = username.lower().strip().lstrip("@")

        # Strategy 1: Web Profile Info
        try:
            url1 = IG_WEB_PROFILE_INFO_URL.format(username=username)
            h1 = {
                "Referer": f"{IG_BASE_URL}/{username}/",
                "X-IG-App-ID": IG_APP_ID,
            }
            res1 = self._make_request(url1, headers=h1)
            if res1 and isinstance(res1, dict):
                uid = res1.get("data", {}).get("user", {}).get("id")
                if uid:
                    logger.info(
                        f"Resolved User ID {uid} via Strategy 1 (web_profile_info)"
                    )
                    return str(uid)
        except Exception:
            pass

        # Strategy 2: Mobile Username Info & Lookup
        try:
            url2 = IG_USER_INFO_MOBILE_URL.format(username=username)
            h2 = {"User-Agent": MOBILE_USER_AGENT, "X-IG-App-ID": IG_APP_ID}
            res2 = self._make_request(url2, headers=h2)
            if res2 and isinstance(res2, dict):
                uid = (
                    res2.get("user", {}).get("pk")
                    or res2.get("user", {}).get("id")
                    or res2.get("user", {}).get("pk_id")
                )
                if uid:
                    logger.info(
                        f"Resolved User ID {uid} via Strategy 2 (mobile usernameinfo)"
                    )
                    return str(uid)

            url2_lookup = f"{IG_USER_LOOKUP_URL}?q={username}"
            res2_lookup = self._make_request(url2_lookup, headers=h2)
            if res2_lookup and isinstance(res2_lookup, dict):
                uid = res2_lookup.get("user", {}).get("pk") or res2_lookup.get(
                    "user", {}
                ).get("id")
                if uid:
                    logger.info(
                        f"Resolved User ID {uid} via Strategy 2 (mobile lookup)"
                    )
                    return str(uid)
        except Exception:
            pass

        # Strategy 3: HTML Scrape Regex
        try:
            url3 = f"{IG_BASE_URL}/{username}/"
            html = self._fetch_html(url3)
            if html:
                patterns = [
                    r'"user_id"\s*:\s*"(\d+)"',
                    r'"profile_id"\s*:\s*"(\d+)"',
                    r'"owner"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"',
                    r'"target_id"\s*:\s*"(\d+)"',
                    r'"props"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"',
                    r'"id"\s*:\s*"(\d{6,})"',
                    r"instagram://user\?id=(\d+)",
                    r'content="instagram://user\?id=(\d+)"',
                    r"users/(\d+)/",
                ]
                for pat in patterns:
                    m = re.search(pat, html)
                    if m:
                        uid = m.group(1)
                        logger.info(
                            f"Resolved User ID {uid} via Strategy 3 (HTML regex)"
                        )
                        return uid
        except Exception:
            pass

        # Strategy 4: yt-dlp fallback extraction
        if yt_dlp is not None:
            try:
                ydl_opts = {
                    "extract_flat": True,
                    "quiet": True,
                    "no_warnings": True,
                    "ignoreerrors": True,
                    "skip_download": True,
                }
                if self.cookie_file and os.path.exists(self.cookie_file):
                    ydl_opts["cookiefile"] = self.cookie_file
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(
                        f"{IG_BASE_URL}/{username}/", download=False
                    )
                    if info:
                        if info.get("channel_id"):
                            return str(info["channel_id"])
                        if info.get("uploader_id"):
                            return str(info["uploader_id"])
                        if info.get("id"):
                            return str(info["id"])
            except Exception:
                pass

        return None

    def _extract_media_card(
        self, media: Dict[str, Any], fallback_username: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Formats raw Instagram media dictionary into a normalized card dictionary.
        Extracts thumbnail, direct video URL (if available), duration, and stats.
        """
        if not media or not isinstance(media, dict):
            return None

        # Unwrap if nested under 'media'
        item = media.get("media", media)

        media_id = str(item.get("id") or item.get("pk") or "")
        if "_" in media_id:
            media_id = media_id.split("_")[0]

        shortcode = item.get("code") or item.get("shortcode") or ""
        if not shortcode and media_id.isdigit():
            shortcode = id_to_shortcode(int(media_id))
        elif not shortcode and media_id:
            shortcode = media_id

        if not shortcode:
            return None

        # Thumbnail extraction
        candidates = item.get("image_versions2", {}).get("candidates", [])
        thumbnail_url = ""
        if candidates and isinstance(candidates, list):
            thumbnail_url = candidates[0].get("url", "")
        if not thumbnail_url:
            thumbnail_url = (
                item.get("display_uri")
                or item.get("display_url")
                or item.get("thumbnail_src")
                or ""
            )

        # Video direct stream URL extraction (if available)
        video_versions = item.get("video_versions", [])
        video_url = ""
        if video_versions and isinstance(video_versions, list):
            video_url = video_versions[0].get("url", "")
        if not video_url:
            video_url = item.get("video_url") or ""

        # Caption extraction
        caption_obj = item.get("caption")
        caption_text = ""
        if isinstance(caption_obj, dict):
            caption_text = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            caption_text = caption_obj
        elif "edge_media_to_caption" in item:
            edges = item.get("edge_media_to_caption", {}).get("edges", [])
            if edges and isinstance(edges[0], dict):
                caption_text = edges[0].get("node", {}).get("text", "")

        # Username extraction
        user_obj = item.get("user", {})
        item_username = (
            user_obj.get("username")
            if isinstance(user_obj, dict)
            else fallback_username
        )
        if not item_username:
            item_username = fallback_username

        # Media type determination
        media_type_raw = item.get("media_type")
        is_video = (
            (media_type_raw == MEDIA_TYPE_VIDEO)
            or (media_type_raw == 2)
            or item.get("is_video", False)
            or bool(video_url)
            or (item.get("product_type") == "clips")
        )
        is_carousel = (
            (media_type_raw == MEDIA_TYPE_CAROUSEL)
            or (media_type_raw == 8)
            or ("carousel_media" in item and item["carousel_media"])
            or (item.get("carousel_media_count", 0) > 0)
        )

        if is_carousel:
            badge_type = "carousel"
        elif is_video:
            badge_type = "reel"
        else:
            badge_type = "image"

        url = (
            f"{IG_BASE_URL}/reel/{shortcode}/"
            if (is_video and not is_carousel)
            else f"{IG_BASE_URL}/p/{shortcode}/"
        )

        duration = float(item.get("video_duration") or 0.0)
        view_count = (
            item.get("play_count")
            or item.get("view_count")
            or item.get("ig_play_count")
            or 0
        )
        like_count = (
            item.get("like_count") or item.get("edge_liked_by", {}).get("count") or 0
        )

        first_line = caption_text.strip().split("\n")[0].strip() if caption_text else ""
        card_title = (
            first_line[:90]
            if first_line
            else (
                f"@{item_username} Video / Reel"
                if badge_type == "reel"
                else f"@{item_username} Post"
            )
        )

        return {
            "id": media_id or shortcode,
            "shortcode": shortcode,
            "title": card_title,
            "username": item_username,
            "url": url,
            "thumbnail_url": thumbnail_url,
            "video_url": video_url,
            "download_url": video_url,
            "caption": caption_text,
            "duration": duration,
            "view_count": view_count,
            "like_count": like_count,
            "media_type": badge_type,
            "quality": self.quality_preset,
            "selected": True,
            "status": "ready",
        }

    def _fetch_all_reels_web(self, username: str, user_id: str) -> None:
        """
        Multi-tier reels crawler engine:
        - Tier 1: Clips API pagination (i.instagram.com/api/v1/clips/user/)
        - Tier 2: Strict video-only feed pagination (i.instagram.com/api/v1/feed/user/{user_id}/)
        - Tier 3: yt-dlp profile flat extraction fallback
        """
        initial_count = len(self.seen_ids)
        self.status_message.emit(
            f"Inspecting Reels for @{username} (Tier 1: Clips API)..."
        )

        # --- TIER 1: Clips API Pagination ---
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
                # Fallback to GET request
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

            new_in_page = 0
            for item in items:
                if self.is_cancelled:
                    return

                # Strict reels filtering: must be standalone video, reject carousels & photos
                if not is_standalone_video(item):
                    continue

                card = self._extract_media_card(item, fallback_username=username)
                if card:
                    sc = card["shortcode"]
                    if sc not in self.seen_ids:
                        self.seen_ids.add(sc)
                        self.item_found.emit(card)
                        new_in_page += 1

            tier1_pages += 1
            self.status_message.emit(
                f"@{username}: Found {len(self.seen_ids)} reels (Page {tier1_pages})..."
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
            time.sleep(REQUEST_DELAY_SECONDS)

        # --- TIER 2: Strict Video-Only Feed Pagination ---
        # Scan user feed for creator profiles where reels may be stored in timeline feed
        self.status_message.emit(
            f"Checking creator feed for @{username} (Tier 2: Timeline Reels)..."
        )
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

                # Strict filter rule: only standalone video reels, skip photo & carousel
                if not is_standalone_video(item):
                    continue

                card = self._extract_media_card(item, fallback_username=username)
                if card:
                    sc = card["shortcode"]
                    if sc not in self.seen_ids:
                        self.seen_ids.add(sc)
                        self.item_found.emit(card)

            tier2_pages += 1
            more_available = res_feed.get("more_available", False)
            next_max_id = res_feed.get("next_max_id") or res_feed.get("max_id")

            if not more_available or not next_max_id or next_max_id == feed_max_id:
                break
            feed_max_id = next_max_id
            time.sleep(REQUEST_DELAY_SECONDS)

        # --- TIER 3: yt-dlp Fallback ---
        if len(self.seen_ids) == initial_count and not self.is_cancelled:
            self.status_message.emit(
                f"Scraping reels via yt-dlp fallback for @{username}..."
            )
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/{username}/", default_username=username
            )

    def _inspect_via_ytdlp(self, url: str, default_username: str = "") -> None:
        """Fallback extraction using yt-dlp engine with clean URL passing and error resilience."""
        if yt_dlp is None:
            self.error.emit("yt-dlp engine is not installed or available.")
            return

        try:
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
                "skip_download": True,
                "socket_timeout": DEFAULT_REQUEST_TIMEOUT,
            }
            if self.cookie_file and os.path.exists(self.cookie_file):
                ydl_opts["cookiefile"] = self.cookie_file

            # Clean URL: strip /reels/ suffix when passing profile URL to yt-dlp
            clean_url = url
            if "/reels" in clean_url:
                clean_url = re.sub(r"/reels/?.*$", "/", clean_url)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(clean_url, download=False)
                except Exception as ex:
                    logger.debug(f"yt-dlp extract_info error: {ex}")
                    info = None

                if not info:
                    return

                entries = info.get("entries", []) or [info]
                for entry in entries:
                    if self.is_cancelled:
                        return
                    if not entry or not isinstance(entry, dict):
                        continue
                    code = (
                        entry.get("id")
                        or entry.get("url", "").rstrip("/").split("/")[-1]
                    )
                    if not code or code in self.seen_ids:
                        continue

                    entry_url = entry.get("url") or f"{IG_BASE_URL}/reel/{code}/"
                    if not entry_url.startswith("http"):
                        entry_url = f"{IG_BASE_URL}/reel/{code}/"

                    item_user = (
                        entry.get("uploader")
                        or entry.get("channel")
                        or default_username
                    )

                    self.seen_ids.add(code)
                    card = {
                        "id": code,
                        "shortcode": code,
                        "title": entry.get("title") or f"Instagram Reel {code}",
                        "username": item_user,
                        "url": entry_url,
                        "thumbnail_url": entry.get("thumbnail") or "",
                        "video_url": "",
                        "download_url": "",
                        "caption": entry.get("description") or "",
                        "duration": float(entry.get("duration") or 0.0),
                        "view_count": entry.get("view_count") or 0,
                        "like_count": entry.get("like_count") or 0,
                        "media_type": "reel",
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    self.item_found.emit(card)
        except Exception as ex:
            self.error.emit(f"yt-dlp error: {str(ex)}")

    def _inspect_single_post(self, shortcode: str) -> None:
        """Resolves a single Instagram post, reel, or TV video."""
        # 1. Mobile media info endpoint
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
                    items = res_m.get("items", [])
                    if items:
                        card = self._extract_media_card(items[0])
                        if card:
                            sc = card["shortcode"]
                            if sc not in self.seen_ids:
                                self.seen_ids.add(sc)
                                self.item_found.emit(card)
                                return
        except Exception:
            pass

        # 2. Web info endpoint
        url = f"{IG_BASE_URL}/p/{shortcode}/?__a=1&__d=dis"
        res = self._make_request(url)
        if res and isinstance(res, dict):
            items = res.get("items", [])
            if items:
                media = items[0]
                card = self._extract_media_card(media)
                if card:
                    sc = card["shortcode"]
                    if sc not in self.seen_ids:
                        self.seen_ids.add(sc)
                        self.item_found.emit(card)
                        return

        # 3. Fallback to yt-dlp
        self._inspect_via_ytdlp(f"{IG_BASE_URL}/p/{shortcode}/")

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

                if ttype == "profile_reels" and username:
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
                        self._fetch_all_reels_web(username, user_id)
                    else:
                        self.status_message.emit(
                            f"Could not resolve ID for @{username}. Trying yt-dlp fallback..."
                        )
                        self._inspect_via_ytdlp(
                            f"{IG_BASE_URL}/{username}/", default_username=username
                        )

                elif ttype in ("reel", "post", "tv") and shortcode:
                    self._inspect_single_post(shortcode)

                elif ttype == "highlight":
                    self._inspect_via_ytdlp(raw_target)

                elif ttype == "story" and username:
                    self._inspect_via_ytdlp(raw_target, default_username=username)

                else:
                    # Unknown target -> fallback to yt-dlp directly
                    self._inspect_via_ytdlp(raw_target)

                pct = int(10 + (idx + 1) / total_targets * 80)
                self.progress.emit(pct)

            self.progress.emit(100)
            self.status_message.emit(
                f"Inspection completed! Found {len(self.seen_ids)} items ready."
            )
            self.finished.emit(len(self.seen_ids))

        except Exception as e:
            self.error.emit(f"Inspection error: {str(e)}")
            self.finished.emit(len(self.seen_ids))
