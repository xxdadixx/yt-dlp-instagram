"""
core/inspect_worker.py - Instagram Media Inspection Worker with Multi-Tier Reels Engine.
"""

import json
import re
import urllib.parse
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp

from config.constants import (
    APP_ID,
    CLIPS_API_URL,
    DESKTOP_USER_AGENT,
    GRAPHQL_REELS_QUERY_HASH,
    GRAPHQL_URL,
    MOBILE_USER_AGENT,
    USER_FEED_API_URL,
    WEB_PROFILE_INFO_URL,
)
from core.cookie_manager import CookieManager, get_cookie_opener
from utils.logger import get_logger

logger = get_logger(__name__)


class InspectWorker(QThread):
    item_found = pyqtSignal(dict)
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(
        self, targets: list[dict], cookie_path: str | None = None, parent=None
    ):
        super().__init__(parent)
        self.targets = targets
        self.cookie_path = cookie_path
        self._is_running = True
        self.opener = get_cookie_opener(cookie_path)
        self.csrf_token = CookieManager.extract_csrf_token(cookie_path) or "missing"

    def stop(self):
        self._is_running = False

    def run(self):
        total_found = 0
        total_targets = len(self.targets)

        for idx, target in enumerate(self.targets):
            if not self._is_running:
                break

            target_type = target.get("type", "single_post")
            url = target.get("url", "")
            username = target.get("username", "")

            self.status.emit(f"Inspecting: {url}")

            try:
                if target_type == "profile_reels":
                    found = self._fetch_all_reels_web(username, url)
                elif target_type in ["profile_posts", "profile"]:
                    found = self._fetch_user_feed(username, url)
                elif target_type in ["stories", "highlights"]:
                    found = self._fetch_via_ytdlp(url)
                else:
                    found = self._fetch_single_media(url)

                total_found += found
            except Exception as e:
                logger.error(f"Error inspecting {url}: {e}", exc_info=True)
                self.error.emit(f"Failed to inspect {url}: {str(e)}")

            if total_targets > 0:
                self.progress.emit(int(((idx + 1) / total_targets) * 100))

        self.finished.emit(total_found)

    # --------------------------------------------------------------------------
    # User ID Resolution (3-Tier Strategy)
    # --------------------------------------------------------------------------
    def _get_user_id(self, username: str) -> str | None:
        """Resolve numeric Instagram User ID using 3 distinct fallback strategies."""
        # 1. Web Profile Info API
        try:
            req_url = f"{WEB_PROFILE_INFO_URL}?username={urllib.parse.quote(username)}"
            headers = {
                "User-Agent": DESKTOP_USER_AGENT,
                "X-IG-App-ID": APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://www.instagram.com/{username}/reels/",
            }
            req = urllib.request.Request(req_url, headers=headers)
            with self.opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_id = data.get("data", {}).get("user", {}).get("id")
                if user_id:
                    return str(user_id)
        except Exception as e:
            logger.debug(f"[User ID] Web Profile Info failed for @{username}: {e}")

        # 2. Mobile Username Info API
        try:
            req_url = f"https://i.instagram.com/api/v1/users/{urllib.parse.quote(username)}/usernameinfo/"
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "X-IG-App-ID": APP_ID,
            }
            req = urllib.request.Request(req_url, headers=headers)
            with self.opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_id = data.get("user", {}).get("pk") or data.get("user", {}).get(
                    "id"
                )
                if user_id:
                    return str(user_id)
        except Exception as e:
            logger.debug(f"[User ID] Username info failed for @{username}: {e}")

        # 3. Direct HTML Regex Scraping
        try:
            page_url = f"https://www.instagram.com/{username}/"
            req = urllib.request.Request(
                page_url, headers={"User-Agent": DESKTOP_USER_AGENT}
            )
            with self.opener.open(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(
                    r'"(?:user_id|profile_id|owner_id|id)":\s*"(\d+)"', html
                )
                if matches:
                    return matches[0]
        except Exception as e:
            logger.debug(f"[User ID] HTML regex scraping failed for @{username}: {e}")

        return None

    # --------------------------------------------------------------------------
    # Multi-Tier Reels Engine
    # --------------------------------------------------------------------------
    def _fetch_all_reels_web(self, username: str, original_url: str) -> int:
        """Fetches ALL reels using Clips API pagination, strict video feed fallback, and yt-dlp."""
        seen_shortcodes = set()
        count = 0

        user_id = self._get_user_id(username) if username else None

        # ======================================================================
        # Tier 1: Clips API Pagination (Primary Reels Endpoint)
        # ======================================================================
        if user_id and self._is_running:
            max_id = ""
            has_more = True
            logger.info(
                f"Starting Tier 1 Clips API crawling for @{username} (ID: {user_id})"
            )

            while has_more and self._is_running:
                try:
                    payload = {
                        "target_user_id": str(user_id),
                        "page_size": "50",
                        "include_feed_video": "true",
                    }
                    if max_id:
                        payload["max_id"] = max_id

                    post_data = urllib.parse.urlencode(payload).encode("utf-8")
                    req = urllib.request.Request(
                        CLIPS_API_URL,
                        data=post_data,
                        headers={
                            "User-Agent": MOBILE_USER_AGENT,
                            "X-IG-App-ID": APP_ID,
                            "X-CSRFToken": self.csrf_token,
                            "X-ASBD-ID": "129477",
                            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "Origin": "https://www.instagram.com",
                            "Referer": f"https://www.instagram.com/{username}/reels/",
                        },
                    )

                    with self.opener.open(req, timeout=15) as resp:
                        res_data = json.loads(resp.read().decode("utf-8"))

                    items = res_data.get("items", [])
                    if not items:
                        break

                    new_items_in_batch = 0
                    for item in items:
                        media = item.get("media", item)
                        shortcode = media.get("code")
                        if not shortcode or shortcode in seen_shortcodes:
                            continue

                        card_data = self._build_media_card(
                            media, username, default_type="Video / Reel"
                        )
                        if card_data:
                            seen_shortcodes.add(shortcode)
                            self.item_found.emit(card_data)
                            count += 1
                            new_items_in_batch += 1

                    paging_info = res_data.get("paging_info", {})
                    has_more = paging_info.get("more_available", False)
                    max_id = paging_info.get("max_id")

                    if not max_id or new_items_in_batch == 0:
                        break

                except Exception as e:
                    logger.warning(f"Tier 1 Clips API page error: {e}")
                    break

        # ======================================================================
        # Tier 2: Strict Video-Only Feed Pagination (For Creator/Model Accounts)
        # ======================================================================
        if count == 0 and user_id and self._is_running:
            logger.info(
                f"Clips API returned 0 items. Engaging Tier 2 Video-Only Feed for @{username}"
            )
            next_max_id = ""
            more_available = True

            while more_available and self._is_running:
                try:
                    feed_url = f"{USER_FEED_API_URL.format(user_id=user_id)}"
                    if next_max_id:
                        feed_url += f"?max_id={urllib.parse.quote(next_max_id)}"

                    req = urllib.request.Request(
                        feed_url,
                        headers={
                            "User-Agent": DESKTOP_USER_AGENT,
                            "X-IG-App-ID": APP_ID,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": f"https://www.instagram.com/{username}/",
                        },
                    )

                    with self.opener.open(req, timeout=15) as resp:
                        res_data = json.loads(resp.read().decode("utf-8"))

                    items = res_data.get("items", [])
                    if not items:
                        break

                    for media in items:
                        media_type = media.get("media_type")
                        is_video = media.get("is_video", False) or media_type == 2

                        # STRICT FILTER: Accept ONLY standalone videos (Reels/Clips)
                        # Strictly reject carousels (media_type == 8) and photos (media_type == 1)
                        if media_type == 8 or media_type == 1 or not is_video:
                            continue

                        shortcode = media.get("code")
                        if not shortcode or shortcode in seen_shortcodes:
                            continue

                        card_data = self._build_media_card(
                            media, username, default_type="Video / Reel"
                        )
                        if card_data:
                            seen_shortcodes.add(shortcode)
                            self.item_found.emit(card_data)
                            count += 1

                    more_available = res_data.get("more_available", False)
                    next_max_id = res_data.get("next_max_id", "")
                    if not next_max_id:
                        break

                except Exception as e:
                    logger.warning(f"Tier 2 Feed API error: {e}")
                    break

        # ======================================================================
        # Tier 3: yt-dlp Engine Fallback
        # ======================================================================
        if count == 0 and self._is_running:
            logger.info(
                f"API endpoints exhausted with 0 items. Fallback to yt-dlp engine for @{username}"
            )
            clean_profile_url = (
                f"https://www.instagram.com/{username}/"
                if username
                else original_url.replace("/reels/", "/").replace("/reels", "/")
            )
            count += self._fetch_via_ytdlp(
                clean_profile_url, filter_reels_only=True, seen=seen_shortcodes
            )

        return count

    # --------------------------------------------------------------------------
    # Single Media & Feed Parsers
    # --------------------------------------------------------------------------
    def _fetch_single_media(self, url: str) -> int:
        return self._fetch_via_ytdlp(url)

    def _fetch_user_feed(self, username: str, original_url: str) -> int:
        user_id = self._get_user_id(username) if username else None
        count = 0
        seen = set()

        if user_id:
            next_max_id = ""
            more_available = True
            while more_available and self._is_running:
                try:
                    feed_url = f"{USER_FEED_API_URL.format(user_id=user_id)}"
                    if next_max_id:
                        feed_url += f"?max_id={urllib.parse.quote(next_max_id)}"

                    req = urllib.request.Request(
                        feed_url,
                        headers={
                            "User-Agent": DESKTOP_USER_AGENT,
                            "X-IG-App-ID": APP_ID,
                        },
                    )
                    with self.opener.open(req, timeout=15) as resp:
                        res_data = json.loads(resp.read().decode("utf-8"))

                    for media in res_data.get("items", []):
                        shortcode = media.get("code")
                        if not shortcode or shortcode in seen:
                            continue

                        card = self._build_media_card(media, username)
                        if card:
                            seen.add(shortcode)
                            self.item_found.emit(card)
                            count += 1

                    more_available = res_data.get("more_available", False)
                    next_max_id = res_data.get("next_max_id", "")
                    if not next_max_id:
                        break
                except Exception as e:
                    logger.warning(f"User feed error: {e}")
                    break

        if count == 0:
            count = self._fetch_via_ytdlp(original_url)
        return count

    def _fetch_via_ytdlp(
        self, url: str, filter_reels_only: bool = False, seen: set | None = None
    ) -> int:
        """Extract media entries using yt-dlp flat extraction."""
        seen = seen if seen is not None else set()
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        if self.cookie_path and CookieManager.is_cookie_valid(self.cookie_path):
            ydl_opts["cookiefile"] = self.cookie_path

        count = 0
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return 0

                entries = info.get("entries", [info]) if "entries" in info else [info]
                for entry in entries:
                    if not entry:
                        continue

                    shortcode = entry.get("id") or entry.get("url")
                    if not shortcode or shortcode in seen:
                        continue

                    entry_url = (
                        entry.get("webpage_url")
                        or entry.get("url")
                        or f"https://www.instagram.com/reel/{shortcode}/"
                    )
                    uploader = (
                        entry.get("uploader")
                        or entry.get("channel")
                        or "instagram_user"
                    )

                    card_data = {
                        "id": shortcode,
                        "url": entry_url,
                        "thumbnail_url": entry.get("thumbnail")
                        or entry.get("thumbnails", [{}])[0].get("url", ""),
                        "username": uploader,
                        "media_type": "Video / Reel",
                        "title": entry.get("title")
                        or entry.get("description", "")[:60]
                        or f"Reel {shortcode}",
                        "duration": entry.get("duration", 0),
                    }
                    seen.add(shortcode)
                    self.item_found.emit(card_data)
                    count += 1
        except Exception as e:
            logger.error(f"yt-dlp extraction failed: {e}")

        return count

    # --------------------------------------------------------------------------
    # Card Data Builder
    # --------------------------------------------------------------------------
    def _build_media_card(
        self, media: dict, username: str, default_type: str = "Video / Reel"
    ) -> dict | None:
        shortcode = media.get("code")
        if not shortcode:
            return None

        media_type_code = media.get("media_type")
        is_video = media.get("is_video", False) or media_type_code == 2

        if media_type_code == 8:
            type_label = "Carousel"
        elif is_video:
            type_label = "Video / Reel"
        else:
            type_label = "Photo"

        # Extract highest quality thumbnail candidate
        thumbnail_url = ""
        image_candidates = media.get("image_versions2", {}).get("candidates", [])
        if image_candidates:
            thumbnail_url = image_candidates[0].get("url", "")

        user_info = media.get("user", {})
        owner_name = user_info.get("username") or username or "instagram_user"

        caption_text = ""
        caption_dict = media.get("caption")
        if isinstance(caption_dict, dict):
            caption_text = caption_dict.get("text", "")

        return {
            "id": shortcode,
            "url": (
                f"https://www.instagram.com/reel/{shortcode}/"
                if is_video
                else f"https://www.instagram.com/p/{shortcode}/"
            ),
            "thumbnail_url": thumbnail_url,
            "username": owner_name,
            "media_type": type_label,
            "title": (
                caption_text[:60].replace("\n", " ")
                if caption_text
                else f"{type_label} - {shortcode}"
            ),
            "duration": media.get("video_duration", 0),
        }
