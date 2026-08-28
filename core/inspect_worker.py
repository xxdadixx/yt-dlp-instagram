import json
import re
import time
import ssl
import os
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, List, Optional, Set

from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp

from config.constants import (
    DEFAULT_HEADERS,
    DEFAULT_USER_AGENT,
    MOBILE_USER_AGENT,
    IG_APP_ID,
    RESERVED_USERNAMES,
)
from core.parser import parse_instagram_url, normalize_url


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
        self._csrf_token: Optional[str] = self._extract_csrf_token(self.cookie_str)
        self._ssl_ctx = ssl._create_unverified_context()

    def cancel(self) -> None:
        """Flags the worker to terminate gracefully."""
        self.is_cancelled = True

    def _extract_csrf_token(self, cookie_str: str) -> Optional[str]:
        """Extracts csrftoken value from cookie string."""
        if not cookie_str:
            return None
        match = re.search(r"(?:^|;\s*)csrftoken=([^;]+)", cookie_str)
        return match.group(1) if match else None

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
        timeout: int = 15,
    ) -> Optional[Dict[str, Any]]:
        """Executes an HTTP request with headers and returns parsed JSON."""
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
        except Exception:
            return None

    def _fetch_html(self, url: str, timeout: int = 15) -> Optional[str]:
        """Fetches raw HTML document for regex extraction."""
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
        except Exception:
            return None

    def _get_user_id(self, username: str) -> Optional[str]:
        """
        Resolves Instagram numeric User ID using 4 fallback strategies:
        1. Web Profile Info endpoint
        2. Mobile Username Info endpoint
        3. Direct HTML Regex scraping
        4. yt-dlp metadata extraction
        """
        username = username.lower().strip()

        # Strategy 1: Web Profile Info
        url1 = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        h1 = {
            "Referer": f"https://www.instagram.com/{username}/",
            "X-IG-App-ID": IG_APP_ID,
        }
        res1 = self._make_request(url1, headers=h1)
        if res1 and isinstance(res1, dict):
            uid = res1.get("data", {}).get("user", {}).get("id")
            if uid:
                return str(uid)

        # Strategy 2: Mobile Username Info
        url2 = f"https://i.instagram.com/api/v1/users/{username}/usernameinfo/"
        h2 = {"User-Agent": MOBILE_USER_AGENT, "X-IG-App-ID": IG_APP_ID}
        res2 = self._make_request(url2, headers=h2)
        if res2 and isinstance(res2, dict):
            uid = res2.get("user", {}).get("pk") or res2.get("user", {}).get("id")
            if uid:
                return str(uid)

        # Strategy 3: HTML Scrape Regex
        url3 = f"https://www.instagram.com/{username}/"
        html = self._fetch_html(url3)
        if html:
            patterns = [
                r'"user_id"\s*:\s*"(\d+)"',
                r'"profile_id"\s*:\s*"(\d+)"',
                r'"owner"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"',
                r'"id"\s*:\s*"(\d{6,})"',
            ]
            for pat in patterns:
                m = re.search(pat, html)
                if m:
                    return m.group(1)

        # Strategy 4: yt-dlp fallback extraction
        try:
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }
            if self.cookie_file and os.path.exists(self.cookie_file):
                ydl_opts["cookiefile"] = self.cookie_file
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.instagram.com/{username}/", download=False
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
        """
        if not media or not isinstance(media, dict):
            return None

        media_id = str(media.get("id") or media.get("pk") or "")
        shortcode = media.get("code") or media.get("shortcode") or ""
        if not shortcode and media_id:
            shortcode = media_id

        if not shortcode:
            return None

        # Thumbnail extraction
        candidates = media.get("image_versions2", {}).get("candidates", [])
        thumbnail_url = ""
        if candidates and isinstance(candidates, list):
            thumbnail_url = candidates[0].get("url", "")
        if not thumbnail_url:
            thumbnail_url = (
                media.get("display_uri")
                or media.get("display_url")
                or media.get("thumbnail_src")
                or ""
            )

        # Caption extraction
        caption_obj = media.get("caption")
        caption_text = ""
        if isinstance(caption_obj, dict):
            caption_text = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            caption_text = caption_obj

        # Username extraction
        user_obj = media.get("user", {})
        item_username = (
            user_obj.get("username")
            if isinstance(user_obj, dict)
            else fallback_username
        )
        if not item_username:
            item_username = fallback_username

        # Media type classification
        media_type_raw = media.get("media_type")
        is_video = media.get("is_video", False) or media_type_raw == 2
        is_carousel = media_type_raw == 8 or "carousel_media" in media

        if is_carousel:
            badge_type = "carousel"
        elif is_video:
            badge_type = "reel"
        else:
            badge_type = "image"

        url = (
            f"https://www.instagram.com/reel/{shortcode}/"
            if (is_video and not is_carousel)
            else f"https://www.instagram.com/p/{shortcode}/"
        )

        duration = float(media.get("video_duration") or 0.0)
        view_count = media.get("play_count") or media.get("view_count") or 0
        like_count = media.get("like_count") or 0

        return {
            "id": media_id or shortcode,
            "shortcode": shortcode,
            "title": (
                f"@{item_username} Video / Reel"
                if badge_type == "reel"
                else f"@{item_username} Post"
            ),
            "username": item_username,
            "url": url,
            "thumbnail_url": thumbnail_url,
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
        while not self.is_cancelled:
            payload: Dict[str, str] = {
                "target_user_id": str(user_id),
                "page_size": "50",
                "include_feed_video": "true",
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
                "https://i.instagram.com/api/v1/clips/user/",
                headers=h,
                data=encoded_data,
                method="POST",
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
                media = item.get("media", item)
                mtype = media.get("media_type")
                is_vid = media.get("is_video", False) or mtype == 2
                is_car = mtype == 8 or "carousel_media" in media

                # Strict filtering: standalone video reels only
                if not is_vid or is_car or mtype == 1:
                    continue

                card = self._extract_media_card(media, fallback_username=username)
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
            more_available = paging_info.get("more_available", False)
            next_max_id = paging_info.get("max_id")

            if not more_available or not next_max_id or next_max_id == max_id:
                break
            max_id = next_max_id
            time.sleep(0.2)

        # --- TIER 2: Strict Video-Only Feed Pagination ---
        self.status_message.emit(
            f"Checking creator feed for @{username} (Tier 2: Timeline Reels)..."
        )
        feed_max_id: Optional[str] = None
        tier2_pages = 0
        while not self.is_cancelled:
            feed_url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/"
            if feed_max_id:
                feed_url += f"?max_id={feed_max_id}"

            h_feed = {
                "User-Agent": MOBILE_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
            }
            res_feed = self._make_request(feed_url, headers=h_feed)
            if not res_feed or not isinstance(res_feed, dict):
                break

            items_feed = res_feed.get("items", [])
            if not items_feed:
                break

            for item in items_feed:
                if self.is_cancelled:
                    return
                mtype = item.get("media_type")
                is_car = mtype == 8 or "carousel_media" in item
                is_photo = mtype == 1
                is_vid = mtype == 2 or (
                    item.get("is_video", False) and not is_car and not is_photo
                )

                # Strict filter rule: only standalone video reels, skip photo & carousel
                if is_photo or is_car or not is_vid:
                    continue

                card = self._extract_media_card(item, fallback_username=username)
                if card:
                    sc = card["shortcode"]
                    if sc not in self.seen_ids:
                        self.seen_ids.add(sc)
                        self.item_found.emit(card)

            tier2_pages += 1
            more_available = res_feed.get("more_available", False)
            next_max_id = res_feed.get("next_max_id")

            if not more_available or not next_max_id or next_max_id == feed_max_id:
                break
            feed_max_id = next_max_id
            time.sleep(0.2)

        # --- TIER 3: yt-dlp Fallback ---
        if len(self.seen_ids) == initial_count and not self.is_cancelled:
            self.status_message.emit(
                f"Scraping reels via yt-dlp fallback for @{username}..."
            )
            self._inspect_via_ytdlp(f"https://www.instagram.com/{username}/")

    def _inspect_via_ytdlp(self, url: str) -> None:
        """Fallback extraction using yt-dlp engine."""
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
                info = ydl.extract_info(url, download=False)
                if not info:
                    return

                entries = info.get("entries", []) or [info]
                for entry in entries:
                    if self.is_cancelled:
                        return
                    if not entry:
                        continue
                    code = (
                        entry.get("id")
                        or entry.get("url", "").rstrip("/").split("/")[-1]
                    )
                    if not code or code in self.seen_ids:
                        continue

                    entry_url = (
                        entry.get("url") or f"https://www.instagram.com/reel/{code}/"
                    )
                    if not entry_url.startswith("http"):
                        entry_url = f"https://www.instagram.com/reel/{code}/"

                    self.seen_ids.add(code)
                    card = {
                        "id": code,
                        "shortcode": code,
                        "title": entry.get("title") or f"Instagram Reel {code}",
                        "username": entry.get("uploader") or entry.get("channel") or "",
                        "url": entry_url,
                        "thumbnail_url": entry.get("thumbnail") or "",
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
        url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
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

        self._inspect_via_ytdlp(f"https://www.instagram.com/p/{shortcode}/")

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
                            f"https://www.instagram.com/{username}/"
                        )

                elif ttype == "profile" and username:
                    user_id = self._get_user_id(username)
                    if user_id:
                        self._fetch_all_reels_web(username, user_id)
                    else:
                        self._inspect_via_ytdlp(
                            f"https://www.instagram.com/{username}/"
                        )

                elif ttype in ("reel", "post", "tv") and shortcode:
                    self._inspect_single_post(shortcode)

                elif ttype == "highlight":
                    self._inspect_via_ytdlp(raw_target)

                elif ttype == "story" and username:
                    self._inspect_via_ytdlp(raw_target)

                else:
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
