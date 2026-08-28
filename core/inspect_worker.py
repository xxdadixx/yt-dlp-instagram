"""
Instagram Media Inspection Worker
Multi-tier crawler for Reels tabs, creator profiles, single posts, and stories.
"""

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import yt_dlp
from PyQt6.QtCore import QThread, pyqtSignal

from config.constants import (
    DEFAULT_USER_AGENT,
    IG_APP_ID,
    MOBILE_USER_AGENT,
)
from core.parser import parse_instagram_url
from utils.logger import logger

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from config.constants import (
        DEFAULT_USER_AGENT,
        MOBILE_USER_AGENT,
        IG_APP_ID,
        IG_ASBD_ID,
    )
except ImportError:
    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 290.0.0.13.111"
    IG_APP_ID = "936619743392459"
    IG_ASBD_ID = "129477"

try:
    from core.parser import parse_instagram_url, normalize_url
except ImportError:

    def parse_instagram_url(url: str) -> Dict[str, Any]:
        return {"url": url, "type": "unknown"}

    def normalize_url(url: str) -> str:
        return url


try:
    from utils.logger import logger
except ImportError:
    import logging

    logger = logging.getLogger("InspectWorker")


class InspectWorker(QThread):
    progress = pyqtSignal(int, str)  # (percentage, status_text)
    card_ready = pyqtSignal(dict)  # (media_item_data)
    finished_inspection = pyqtSignal(int)  # (total_found_items)
    error = pyqtSignal(str)  # (error_message)

    def __init__(
        self,
        targets: List[str],
        cookie_file: Optional[str] = None,
        cookie_str: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.targets = targets
        self.cookie_file = cookie_file
        self.cookie_str = cookie_str
        self._is_cancelled = False
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def cancel(self):
        self._is_cancelled = True

    # -------------------------------------------------------------------------
    # Network & Cookie Session Management
    # -------------------------------------------------------------------------

    def _build_network_opener(self) -> urllib.request.OpenerDirector:
        """Constructs an urllib opener with SSL bypass and cookie jar."""
        if self.cookie_file and os.path.exists(self.cookie_file):
            try:
                self.cookie_jar.load(
                    self.cookie_file, ignore_discard=True, ignore_expires=True
                )
            except Exception as e:
                logger.warning(f"Could not load cookies via MozillaCookieJar: {e}")
                self._load_cookies_manual(self.cookie_file)

        self.csrf_token = self._get_cookie_value("csrftoken") or ""

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
        cookie_handler = urllib.request.HTTPCookieProcessor(self.cookie_jar)
        return urllib.request.build_opener(cookie_handler, https_handler)

    def _load_cookies_manual(self, filepath: str):
        """Fallback line-by-line parser for Netscape cookies.txt files."""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        domain, flag, path, secure, expires, name, value = parts[:7]
                        c = http.cookiejar.Cookie(
                            version=0,
                            name=name,
                            value=value,
                            port=None,
                            port_specified=False,
                            domain=domain,
                            domain_specified=True,
                            domain_initial_dot=domain.startswith("."),
                            path=path,
                            path_specified=True,
                            secure=secure.lower() == "true",
                            expires=int(expires) if expires.isdigit() else None,
                            discard=False,
                            comment=None,
                            comment_url=None,
                            rest={"HttpOnly": None},
                            rfc2109=False,
                        )
                        self.cookie_jar.set_cookie(c)
        except Exception as e:
            logger.error(f"Manual cookie loading failed: {e}")

    def _get_cookie_value(self, name: str) -> Optional[str]:
        """Retrieves a specific cookie value from the active jar."""
        for cookie in self.cookie_jar:
            if cookie.name == name:
                return cookie.value
        return None

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Executes an HTTP request and parses JSON response."""
        if self._is_cancelled:
            return None

        req_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": IG_APP_ID,
            "X-ASBD-ID": IG_ASBD_ID,
            "X-IG-WWW-Claim": "0",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

        if self.csrf_token:
            req_headers["X-CSRFToken"] = self.csrf_token

        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with self.opener.open(req, timeout=18) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            logger.debug(f"HTTP Error {e.code} for {url}")
            return None
        except Exception as e:
            logger.debug(f"Request failed for {url}: {e}")
            return None

    # -------------------------------------------------------------------------
    # Target Execution Lifecycle
    # -------------------------------------------------------------------------

    def run(self):
        total_items_emitted = 0
        self.progress.emit(5, "Initializing media inspection...")

        for idx, target_raw in enumerate(self.targets):
            if self._is_cancelled:
                break

            parsed = parse_instagram_url(target_raw)
            if not parsed:
                logger.warning(f"Unable to parse target URL: {target_raw}")
                continue

            target_type = parsed["type"]
            target_val = parsed["target"]

            self.progress.emit(
                10 + int((idx / max(len(self.targets), 1)) * 80),
                f"Inspecting [{target_type}]: {target_val}...",
            )

            try:
                if target_type == "profile_reels":
                    found = self._fetch_all_reels_multi_tier(
                        target_val, parsed["clean_url"]
                    )
                    total_items_emitted += found
                elif target_type in ("reel", "post"):
                    found = self._inspect_single_post(
                        target_val, parsed["clean_url"], target_type
                    )
                    total_items_emitted += found
                elif target_type == "profile":
                    found = self._fetch_all_reels_multi_tier(
                        target_val, parsed["clean_url"]
                    )
                    total_items_emitted += found
                else:
                    found = self._inspect_with_ytdlp(parsed["clean_url"])
                    total_items_emitted += found
            except Exception as e:
                logger.error(f"Inspection error on {target_raw}: {e}", exc_info=True)
                self.error.emit(f"Error inspecting {target_val}: {str(e)}")

        self.progress.emit(
            100, f"Inspection completed! Found {total_items_emitted} items ready."
        )
        self.finished_inspection.emit(total_items_emitted)

    # =========================================================================
    # Request Helpers
    # =========================================================================

    def _get_headers(self, is_mobile: bool = False) -> Dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": IG_APP_ID,
        }
        if is_mobile:
            headers.update(
                {
                    "User-Agent": MOBILE_USER_AGENT,
                    "X-IG-Capabilities": "36r/Fx8=",
                    "X-IG-Connection-Type": "WIFI",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                }
            )
        else:
            headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://www.instagram.com/",
                    "X-Requested-With": "XMLHttpRequest",
                }
            )

        if self.cookie_str:
            headers["Cookie"] = self.cookie_str
            csrf_match = re.search(r"csrftoken=([a-zA-Z0-9_-]+)", self.cookie_str)
            if csrf_match:
                headers["X-CSRFToken"] = csrf_match.group(1)

        return headers

    def _http_request(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        is_mobile: bool = False,
    ) -> Optional[Dict[str, Any]]:
        headers = self._get_headers(is_mobile=is_mobile)
        req_data = None
        if data is not None:
            req_data = urllib.parse.urlencode(data).encode("utf-8")

        req = urllib.request.Request(url, data=req_data, headers=headers)
        try:
            with urllib.request.urlopen(
                req, context=self.ssl_context, timeout=15
            ) as resp:
                raw_bytes = resp.read()
                return json.loads(raw_bytes.decode("utf-8", errors="ignore"))
        except Exception as e:
            logger.debug(f"HTTP request failed for {url}: {e}")
            return None

    # -------------------------------------------------------------------------
    # Robust User ID Resolution
    # -------------------------------------------------------------------------

    def _get_user_id(self, username: str) -> Optional[str]:
        """Resolves numeric user ID using Web Profile Info, Mobile API, and HTML Regex."""
        # Strategy 1: Web Profile Info API
        url_web = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        data_web = self._http_request(url_web, is_mobile=False)
        if data_web and "data" in data_web and data_web["data"].get("user"):
            uid = data_web["data"]["user"].get("id")
            if uid:
                return str(uid)

        # Strategy 2: Mobile Lookup API
        url_lookup = f"https://i.instagram.com/api/v1/users/lookup/?username={username}"
        data_lookup = self._http_request(url_lookup, is_mobile=True)
        if data_lookup and "user" in data_lookup:
            uid = data_lookup["user"].get("pk") or data_lookup["user"].get("id")
            if uid:
                return str(uid)

        # Strategy 3: Direct Profile HTML Regex Scanning
        try:
            profile_url = f"https://www.instagram.com/{username}/"
            req = urllib.request.Request(
                profile_url, headers=self._get_headers(is_mobile=False)
            )
            with urllib.request.urlopen(
                req, context=self.ssl_context, timeout=12
            ) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                for pattern in [
                    r'"user_id":"(\d+)"',
                    r'"profile_id":"(\d+)"',
                    r'"owner":\{"id":"(\d+)"\}',
                    r'"props":\{"id":"(\d+)"\}',
                ]:
                    m = re.search(pattern, html)
                    if m:
                        return m.group(1)
        except Exception as e:
            logger.debug(f"HTML scraping user_id fallback failed for @{username}: {e}")

        return None

    # =========================================================================
    # Multi-Tier Reels Inspection Engine
    # =========================================================================

    def _fetch_all_reels_multi_tier(self, username: str, target_url: str) -> int:
        seen_codes = set()
        user_id = self._get_user_id(username)

        # --- Tier 1: Mobile Clips API Pagination (Primary) ---
        if user_id:
            logger.info(
                f"Starting Tier 1 Clips API pagination for @{username} (ID: {user_id})"
            )
            max_id = None
            page_count = 0

            while not self._is_cancelled and page_count < 40:
                payload = {
                    "target_user_id": user_id,
                    "page_size": 50,
                    "include_feed_video": "true",
                }
                if max_id:
                    payload["max_id"] = max_id

                resp = self._http_request(
                    "https://i.instagram.com/api/v1/clips/user/",
                    data=payload,
                    is_mobile=True,
                )
                if not resp or "items" not in resp:
                    break

                items = resp.get("items", [])
                if not items:
                    break

                for item in items:
                    media = item.get("media", item)
                    code = media.get("code")
                    if not code or code in seen_codes:
                        continue

                    # Pure standalone video reel check
                    media_type = media.get("media_type")
                    if media_type not in (2, None) and not media.get("is_video"):
                        continue

                    seen_codes.add(code)
                    card = self._build_card_dict(media, username, "reel")
                    self.card_ready.emit(card)

                paging = resp.get("paging_info", {})
                next_max_id = paging.get("max_id")
                has_next = paging.get("more_available", False) or paging.get(
                    "has_max_id", False
                )

                if not has_next or not next_max_id or next_max_id == max_id:
                    break

                max_id = next_max_id
                page_count += 1
                time.sleep(0.1)

        # --- Tier 2: Strict Video-Only Feed Pagination (For Creator/Model Profiles) ---
        if user_id and len(seen_codes) < 6:
            logger.info(
                f"Tier 1 returned few items. Starting Tier 2 strict feed inspection for @{username}"
            )
            max_id = None
            feed_pages = 0

            while not self._is_cancelled and feed_pages < 15:
                feed_url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/"
                if max_id:
                    feed_url += f"?max_id={max_id}"

                resp = self._http_request(feed_url, is_mobile=True)
                if not resp or "items" not in resp:
                    break

                items = resp.get("items", [])
                if not items:
                    break

                for media in items:
                    # STRICT FILTER: Standalone video ONLY (media_type == 2). Reject Carousel (8) and Photo (1)
                    if media.get("media_type") != 2 and not media.get("is_video"):
                        continue

                    code = media.get("code")
                    if not code or code in seen_codes:
                        continue

                    seen_codes.add(code)
                    card = self._build_card_dict(media, username, "reel")
                    self.card_ready.emit(card)

                if not resp.get("more_available") or not resp.get("next_max_id"):
                    break

                max_id = resp.get("next_max_id")
                feed_pages += 1
                time.sleep(0.1)

        # --- Tier 3: yt-dlp Flat Extraction Fallback ---
        if len(seen_codes) == 0:
            logger.warning(
                f"Direct API scraping yielded 0 reels for @{username}. Falling back to yt-dlp Engine."
            )
            base_profile_url = f"https://www.instagram.com/{username}/"
            ytdlp_found = self._inspect_with_ytdlp(
                base_profile_url, filter_reels_only=True
            )
            return ytdlp_found

        return len(seen_codes)

    # -------------------------------------------------------------------------
    # Multi-Tier Reels Engine
    # -------------------------------------------------------------------------

    def _inspect_reels_tab(self, username: str):
        """Executes full pagination across clips, feeds, and yt-dlp fallback."""
        if not username:
            return

        username = username.strip().lstrip("@")
        self.status.emit(f"Resolving User ID for @{username}...")
        user_id = self._get_user_id(username)

        seen_shortcodes: Set[str] = set()

        if user_id:
            # Tier 1: Clips API Pagination (Dedicated Reels endpoint)
            self._fetch_reels_clips_api(username, user_id, seen_shortcodes)

            # Tier 2: Timeline Feed API (For Creator/Model accounts with feed-indexed reels)
            self._fetch_reels_feed_api(username, user_id, seen_shortcodes)

        # Tier 3: yt-dlp Flat Extraction Fallback
        if len(seen_shortcodes) == 0 and yt_dlp:
            self.status.emit(f"Falling back to yt-dlp extraction for @{username}...")
            self._fetch_reels_ytdlp(username, seen_shortcodes)

    def _fetch_reels_clips_api(
        self, username: str, user_id: str, seen_shortcodes: Set[str]
    ):
        """Tier 1: Queries https://i.instagram.com/api/v1/clips/user/ with pagination."""
        max_id: Optional[str] = None
        self.status.emit(f"Paginating Reels clips for @{username}...")

        while not self._is_cancelled:
            payload = {
                "target_user_id": user_id,
                "page_size": "50",
                "include_feed_video": "true",
            }
            if max_id:
                payload["max_id"] = str(max_id)

            encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }

            resp = self._make_request(
                "https://i.instagram.com/api/v1/clips/user/",
                headers=headers,
                data=encoded_data,
                method="POST",
            )

            if not resp:
                break

            items = resp.get("items", []) or resp.get("grid_items", [])
            if not items:
                break

            new_found = 0
            for item in items:
                media = item.get("media", item)
                m_type = media.get("media_type")

                # Strictly drop carousels and images
                if m_type in (1, 8):
                    continue

                card = self._parse_media_dict(
                    media, username, default_badge="Video / Reel"
                )
                if card and card["shortcode"] not in seen_shortcodes:
                    seen_shortcodes.add(card["shortcode"])
                    self._emit_card(card, username)
                    new_found += 1

            paging_info = resp.get("paging_info", {})
            more_available = paging_info.get("more_available", False)
            next_max_id = paging_info.get("max_id")

            if (
                not more_available
                or not next_max_id
                or next_max_id == max_id
                or new_found == 0
            ):
                break

            max_id = next_max_id
            time.sleep(0.25)

    def _fetch_reels_feed_api(
        self, username: str, user_id: str, seen_shortcodes: Set[str]
    ):
        """
        Tier 2: Timeline feed pagination with strict standalone video filter.
        Ensures creator accounts with timeline reels are indexed without leaking carousels.
        """
        max_id: Optional[str] = None
        self.status.emit(f"Scanning timeline video feed for @{username}...")

        while not self._is_cancelled:
            url = f"https://i.instagram.com/api/v1/feed/user/{user_id}/"
            if max_id:
                url += f"?max_id={urllib.parse.quote(str(max_id))}"

            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
            }

            resp = self._make_request(url, headers=headers)
            if not resp:
                break

            items = resp.get("items", [])
            if not items:
                break

            new_found = 0
            for media in items:
                m_type = media.get("media_type")

                # Strict Filter Rule:
                # ONLY standalone videos (media_type == 2 or is_video=True).
                # Strictly reject carousels (media_type == 8) and photos (media_type == 1).
                if m_type in (1, 8):
                    continue

                is_video = media.get("is_video", False) or (m_type == 2)
                if not is_video:
                    continue

                card = self._parse_media_dict(
                    media, username, default_badge="Video / Reel"
                )
                if card and card["shortcode"] not in seen_shortcodes:
                    seen_shortcodes.add(card["shortcode"])
                    self._emit_card(card, username)
                    new_found += 1

            more_available = resp.get("more_available", False)
            next_max_id = resp.get("next_max_id")

            if (
                not more_available
                or not next_max_id
                or next_max_id == max_id
                or new_found == 0
            ):
                break

            max_id = next_max_id
            time.sleep(0.25)

    def _fetch_reels_ytdlp(self, username: str, seen_shortcodes: Set[str]):
        """Tier 3: Passes clean profile URL to yt-dlp in flat extraction mode."""
        base_url = f"https://www.instagram.com/{username}/"
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        if self.cookie_file and os.path.exists(self.cookie_file):
            ydl_opts["cookiefile"] = self.cookie_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(base_url, download=False)
                if not info:
                    return

                entries = info.get("entries", [])
                for entry in entries:
                    if not entry:
                        continue

                    e_url = entry.get("url") or entry.get("webpage_url") or ""
                    code = entry.get("id")

                    if "/reel/" in e_url or "/p/" in e_url:
                        m = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", e_url)
                        if m:
                            code = m.group(1)

                    if not code or code in seen_shortcodes:
                        continue

                    thumb_url = ""
                    thumbs = entry.get("thumbnails", [])
                    if thumbs:
                        thumb_url = thumbs[-1].get("url", "")
                    elif entry.get("thumbnail"):
                        thumb_url = entry.get("thumbnail")

                    card = {
                        "id": code,
                        "shortcode": code,
                        "url": f"https://www.instagram.com/reel/{code}/",
                        "media_type": "video",
                        "badge_text": "Video / Reel",
                        "title": entry.get("title")
                        or entry.get("description")
                        or f"Reel by @{username}",
                        "thumbnail_url": thumb_url,
                        "username": username,
                        "duration": float(entry.get("duration") or 0.0),
                        "view_count": int(entry.get("view_count") or 0),
                        "like_count": int(entry.get("like_count") or 0),
                        "comment_count": int(entry.get("comment_count") or 0),
                        "timestamp": int(entry.get("timestamp") or 0),
                        "is_video": True,
                        "format_options": [
                            "Best Video (Highest Quality)",
                            "Audio Only (MP3/M4A)",
                        ],
                    }
                    seen_shortcodes.add(code)
                    self._emit_card(card, username)
        except Exception as e:
            logger.error(f"yt-dlp fallback failed for @{username}: {e}")

    # -------------------------------------------------------------------------
    # Single Post, Profile, and Story Inspectors
    # -------------------------------------------------------------------------

    def _inspect_single_post(self, code: str, url: str, target_type: str) -> int:
        info_url = f"https://i.instagram.com/api/v1/media/{code}/info/"
        data = self._http_request(info_url, is_mobile=True)

        if data and "items" in data and len(data["items"]) > 0:
            media = data["items"][0]
            username = media.get("user", {}).get("username", "instagram_user")
            card = self._build_card_dict(media, username, target_type)
            self.card_ready.emit(card)
            return 1

        return self._inspect_with_ytdlp(url)

    def _inspect_with_ytdlp(self, url: str, filter_reels_only: bool = False) -> int:
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
        }
        if self.cookie_file:
            ydl_opts["cookiefile"] = self.cookie_file

        found_count = 0
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return 0

                entries = info.get("entries") if "entries" in info else [info]
                for entry in entries:
                    if not entry:
                        continue

                    e_url = entry.get("url") or entry.get("webpage_url") or url
                    if filter_reels_only and "/reel/" not in e_url:
                        continue

                    code = entry.get("id") or (e_url.rstrip("/").split("/")[-1])
                    uploader = (
                        entry.get("uploader")
                        or entry.get("channel")
                        or "instagram_user"
                    )

                    card = {
                        "id": code,
                        "shortcode": code,
                        "url": (
                            e_url
                            if e_url.startswith("http")
                            else f"https://www.instagram.com/reel/{code}/"
                        ),
                        "title": f"@{uploader} Video / Reel",
                        "uploader": uploader,
                        "thumbnail": entry.get("thumbnail") or "",
                        "duration": entry.get("duration") or 0,
                        "type": "reel" if "/reel/" in e_url else "video",
                        "view_count": entry.get("view_count") or 0,
                        "status": "Ready",
                    }
                    self.card_ready.emit(card)
                    found_count += 1
        except Exception as e:
            logger.debug(f"yt-dlp extraction failed for {url}: {e}")

        return found_count

    # =========================================================================
    # Formatting Helpers
    # =========================================================================

    def _build_card_dict(
        self, media: Dict[str, Any], username: str, item_type: str
    ) -> Dict[str, Any]:
        code = media.get("code") or media.get("id")
        thumb = ""

        # Extract best available thumbnail URL
        if "image_versions2" in media:
            candidates = media["image_versions2"].get("candidates", [])
            if candidates:
                thumb = candidates[0].get("url", "")
        elif "display_url" in media:
            thumb = media.get("display_url", "")

        return {
            "id": code,
            "shortcode": code,
            "url": (
                f"https://www.instagram.com/reel/{code}/"
                if item_type == "reel"
                else f"https://www.instagram.com/p/{code}/"
            ),
            "title": f"@{username} Video / Reel",
            "uploader": username,
            "thumbnail": thumb,
            "duration": media.get("video_duration") or 0,
            "type": item_type,
            "view_count": media.get("play_count") or media.get("view_count") or 0,
            "like_count": media.get("like_count") or 0,
            "comment_count": media.get("comment_count") or 0,
            "status": "Ready",
        }

    def _inspect_profile_all(self, username: str):
        """Inspects all media under a user profile."""
        self._inspect_reels_tab(username)

    def _inspect_story_or_highlight(self, target_dict: Dict[str, Any], url: str):
        """Inspects stories or highlights."""
        if yt_dlp:
            try:
                ydl_opts = {"quiet": True, "skip_download": True, "extract_flat": True}
                if self.cookie_file and os.path.exists(self.cookie_file):
                    ydl_opts["cookiefile"] = self.cookie_file
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    entries = info.get("entries", [info]) if info else []
                    for entry in entries:
                        if not entry:
                            continue
                        sid = entry.get("id", str(int(time.time())))
                        card = {
                            "id": sid,
                            "shortcode": sid,
                            "url": entry.get("webpage_url") or entry.get("url") or url,
                            "media_type": "video",
                            "badge_text": "Story",
                            "title": entry.get("title")
                            or f"Story by {entry.get('uploader', 'user')}",
                            "thumbnail_url": entry.get("thumbnail", ""),
                            "username": entry.get("uploader", "user"),
                            "duration": float(entry.get("duration") or 0.0),
                            "is_video": True,
                            "format_options": [
                                "Best Video (Highest Quality)",
                                "Audio Only (MP3/M4A)",
                            ],
                        }
                        self._emit_card(card, card["username"])
            except Exception as e:
                logger.error(f"Story inspection failed for {url}: {e}")

    def _inspect_generic_url(self, url: str):
        """Generic fallback for unclassified Instagram links."""
        m = re.search(r"/(?:reel|reels|p)/([A-Za-z0-9_-]+)", url)
        if m:
            self._inspect_single_post(m.group(1), url)
        else:
            username = self._extract_username_from_url(url)
            if username:
                self._inspect_reels_tab(username)

    # -------------------------------------------------------------------------
    # Parsing Helpers & Signal Dispatcher
    # -------------------------------------------------------------------------

    def _parse_media_dict(
        self,
        media: Dict[str, Any],
        default_username: str,
        default_badge: str = "Video / Reel",
    ) -> Optional[Dict[str, Any]]:
        """Transforms Instagram API media JSON into a normalized card dictionary."""
        shortcode = media.get("code") or media.get("shortcode")
        if not shortcode:
            shortcode = str(media.get("pk") or media.get("id") or "")
        if not shortcode:
            return None

        caption = ""
        caption_obj = media.get("caption")
        if isinstance(caption_obj, dict):
            caption = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            caption = caption_obj

        thumb_url = ""
        candidates = media.get("image_versions2", {}).get("candidates", [])
        if candidates and isinstance(candidates, list):
            thumb_url = candidates[0].get("url", "")
        if not thumb_url:
            thumb_url = media.get("display_url") or media.get("thumbnail_src") or ""

        user_info = media.get("user", {})
        u_name = user_info.get("username") or default_username

        duration = float(media.get("video_duration", 0.0) or 0.0)
        view_count = int(media.get("play_count") or media.get("view_count") or 0)
        like_count = int(media.get("like_count") or 0)
        comment_count = int(media.get("comment_count") or 0)
        timestamp = int(media.get("taken_at") or media.get("device_timestamp") or 0)

        return {
            "id": shortcode,
            "shortcode": shortcode,
            "url": f"https://www.instagram.com/reel/{shortcode}/",
            "media_type": "video",
            "badge_text": default_badge,
            "title": caption if caption else f"Reel by @{u_name}",
            "thumbnail_url": thumb_url,
            "username": u_name,
            "duration": duration,
            "view_count": view_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "timestamp": timestamp,
            "is_video": True,
            "format_options": ["Best Video (Highest Quality)", "Audio Only (MP3/M4A)"],
        }

    def _emit_card(self, card_data: Dict[str, Any], username: str):
        """Appends result and notifies main GUI thread."""
        self.results.append(card_data)
        self.item_found.emit(card_data)
        count = len(self.results)
        self.progress.emit(count, 0, f"Found {count} items for @{username}...")
        self.status.emit(f"Inspected @{username}: {count} items ready")

    def _extract_username_from_url(self, url: str) -> str:
        """Extracts username handle from clean profile URLs."""
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
        if m:
            candidate = m.group(1).lower()
            if candidate not in ("p", "reel", "reels", "stories", "explore"):
                return candidate
        return "user"
