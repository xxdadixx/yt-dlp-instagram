"""
core/inspect_worker.py - High-speed parallel multi-tier media inspection worker for Instagram.
Features chained multi-tier pagination, detailed system diagnostics, and cookie rate-limit detection.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import logging
import os
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
)
from core.parser import (
    is_standalone_video,
    normalize_url,
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
    progress = pyqtSignal(int)
    item_found = pyqtSignal(dict)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    MAX_CONCURRENT_INSPECTS = 4

    def __init__(
        self,
        targets: List[str],
        cookie_str: Optional[str] = None,
        cookie_file: Optional[str] = None,
        profile_mode: str = "all",  # "all", "reels", "photos"
        quality_preset: str = "best_video",
        parent=None,
    ):
        super().__init__(parent)
        self.targets: List[str] = targets or []
        self.cookie_str: str = (cookie_str or "").strip()
        self.cookie_file: str = (cookie_file or "").strip()
        self.profile_mode: str = (profile_mode or "all").lower()
        self.quality_preset: str = quality_preset
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

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        method: Optional[str] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        caller_tag: str = "",
    ) -> Optional[Dict[str, Any]]:
        if self.is_cancelled:
            return None

        req_headers = dict(DEFAULT_HEADERS)
        if headers:
            req_headers.update(headers)
        if self.cookie_str:
            req_headers["Cookie"] = self.cookie_str
        if self._csrf_token:
            req_headers["X-CSRFToken"] = self._csrf_token

        req_headers["Accept-Encoding"] = "gzip, deflate"
        req_headers.setdefault("Accept", "*/*")
        req_headers.setdefault("Accept-Language", "en-US,en;q=0.9")

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout
            ) as resp:
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
                # ลบ UTF-8 BOM (\ufeff) และตัดช่องว่างส่วนเกิน
                raw = raw.lstrip("\ufeff").strip()

                if raw.startswith(("{", "[")):
                    return json.loads(raw)
                return None
        except urllib.error.HTTPError as e:
            msg = f"[{caller_tag or 'API'}] HTTP {e.code}: {e.reason}"
            logger.debug(msg)
            return None
        except Exception as e:
            logger.debug(f"[{caller_tag or 'API'}] Request to {url} failed: {e}")
            return None

    def _get_user_id(self, username: str) -> Optional[str]:
        username = username.lower().strip().lstrip("@")
        self.status_message.emit(f"🔍 [Resolver] Fetching User ID for @{username}...")

        # Strategy 1: Web Profile Info Endpoint
        try:
            url1 = IG_WEB_PROFILE_INFO_URL.format(username=username)
            h1 = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "X-ASBD-ID": "129477",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{IG_BASE_URL}/{username}/",
                "Origin": IG_BASE_URL,
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            }
            res1 = self._make_request(url1, headers=h1, caller_tag="WebProfileInfo")
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

        # Strategy 2: Web HTML Scraper (สกัดจาก Script Tag กรณี API ติด Rate limit)
        try:
            profile_url = f"{IG_BASE_URL}/{username}/"
            req = urllib.request.Request(
                profile_url,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Cookie": self.cookie_str or "",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
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
                        pass

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
            h3 = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Referer": f"{IG_BASE_URL}/{username}/",
            }
            res3 = self._make_request(url3, headers=h3, caller_tag="TopSearch")
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

        self.status_message.emit(
            f"⚠️ [Resolver] Could not resolve User ID for @{username}."
        )
        return None

    def _extract_media_cards(
        self, item: Dict[str, Any], raw_target: str = "", fallback_username: str = ""
    ) -> List[Dict[str, Any]]:
        if not item or not isinstance(item, dict):
            return []

        media = item.get("media", item)
        shortcode = media.get("code") or media.get("shortcode") or ""
        user_info = media.get("user") or media.get("owner") or {}
        username = user_info.get("username") or fallback_username

        carousel_children = media.get("carousel_media") or [
            edge.get("node", {})
            for edge in media.get("edge_sidecar_to_children", {}).get("edges", [])
        ]

        # Extract caption across REST and GraphQL payloads
        caption_obj = media.get("caption")
        caption_text = ""
        if isinstance(caption_obj, dict):
            caption_text = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            caption_text = caption_obj
        elif "edge_media_to_caption" in media:
            edges = media.get("edge_media_to_caption", {}).get("edges", [])
            if edges and isinstance(edges[0], dict):
                caption_text = edges[0].get("node", {}).get("text", "")

        clean_caption = caption_text.strip()
        caption_lines = [
            line.strip() for line in clean_caption.splitlines() if line.strip()
        ]
        first_line = caption_lines[0] if caption_lines else ""

        title_line = (
            first_line
            if first_line
            else f"Instagram Carousel #{shortcode} ({total} items)"
        )
        title_line = first_line if first_line else f"Instagram {b_type} #{shortcode}"

        # 1. Multi-Item Carousel Post -> Consolidate into ONE Card
        if carousel_children:
            total = len(carousel_children)
            slides = []
            for idx, child in enumerate(carousel_children, start=1):
                child_id = str(child.get("id") or f"{shortcode}_{idx}")
                is_vid = bool(
                    child.get("is_video")
                    or child.get("media_type") == 2
                    or child.get("video_versions")
                    or child.get("__typename") == "GraphVideo"
                )

                v_url = ""
                if is_vid:
                    v_versions = child.get("video_versions") or []
                    v_url = (
                        v_versions[0].get("url", "")
                        if v_versions
                        else child.get("video_url", "")
                    )

                display_url = child.get("display_url", "")
                if "display_resources" in child and child["display_resources"]:
                    best_res = max(
                        child["display_resources"],
                        key=lambda r: r.get("config_width", 0),
                    )
                    full_img_url = best_res.get("src") or display_url
                elif "image_versions2" in child and child["image_versions2"].get(
                    "candidates"
                ):
                    best_res = max(
                        child["image_versions2"]["candidates"],
                        key=lambda r: (r.get("width", 0) * r.get("height", 0)),
                    )
                    full_img_url = best_res.get("url") or display_url
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
                caption_text.splitlines()[0]
                if caption_text
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

        # 2. Single Post / Reel / Image
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

        title_line = (
            caption_text.splitlines()[0]
            if caption_text
            else f"Instagram {b_type} #{shortcode}"
        )

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
        target_url = raw_target or f"https://www.instagram.com/p/{shortcode}/"

        # Tier 1: Instagram Mobile Media Info API
        media_id = shortcode_to_id(shortcode)
        if media_id:
            info_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
            h_mobile = {"User-Agent": MOBILE_USER_AGENT, "X-IG-App-ID": IG_APP_ID}
            if self.cookie_str:
                h_mobile["Cookie"] = self.cookie_str
            if self._csrf_token:
                h_mobile["X-CSRFToken"] = self._csrf_token

            res_mobile = self._make_request(
                info_url, headers=h_mobile, caller_tag="MobileMediaInfo"
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
                    return extracted

        # Tier 2: Instagram Web JSON Endpoint
        api_url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
        headers_web = {
            "User-Agent": DEFAULT_USER_AGENT,
            "X-IG-App-ID": IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
            "Accept": "*/*",
        }
        if self.cookie_str:
            headers_web["Cookie"] = self.cookie_str
        if self._csrf_token:
            headers_web["X-CSRFToken"] = self._csrf_token

        try:
            req = urllib.request.Request(api_url, headers=headers_web)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=8) as resp:
                if resp.status == 200:
                    raw_data = resp.read()
                    if len(raw_data) >= 2 and raw_data[:2] == b"\x1f\x8b":
                        raw_data = gzip.decompress(raw_data)
                    payload = json.loads(raw_data.decode("utf-8", errors="replace"))
                    media_data = (
                        payload.get("graphql", {}).get("shortcode_media")
                        or payload.get("data", {}).get("xdt_shortcode_media")
                        or (
                            payload.get("items", [{}])[0]
                            if "items" in payload and payload["items"]
                            else None
                        )
                    )
                    if media_data:
                        extracted = self._extract_media_cards(
                            media_data, raw_target=target_url
                        )
                        if extracted:
                            with self._lock:
                                for card in extracted:
                                    cid = str(card["id"])
                                    if cid not in self.seen_ids:
                                        self.seen_ids.add(cid)
                                        self.item_found.emit(card)
                            return extracted
        except Exception as e:
            logger.debug(f"Direct API inspection failed for {shortcode}: {e}")

        # Tier 3: Direct yt-dlp fallback
        self._inspect_via_ytdlp(target_url)
        return []

    def _fetch_timeline_feed_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user's timeline media (Photos, Carousels, Videos) via Web REST Feed API."""
        feed_max_id: Optional[str] = None
        feed_pages = 0
        found_count = 0

        while feed_pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            feed_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count=50"
            if feed_max_id:
                feed_url += f"&max_id={feed_max_id}"

            h_feed = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "X-ASBD-ID": "129477",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{IG_BASE_URL}/{username}/",
                "Accept": "*/*",
            }
            if self.cookie_str:
                h_feed["Cookie"] = self.cookie_str
            if self._csrf_token:
                h_feed["X-CSRFToken"] = self._csrf_token

            res_feed = self._make_request(
                feed_url, headers=h_feed, caller_tag="WebTimelineFeed"
            )
            if not res_feed or not isinstance(res_feed, dict):
                break

            items_feed = res_feed.get("items", [])
            if not items_feed:
                break

            for item in items_feed:
                if self.is_cancelled:
                    return found_count
                media_item = item.get("media", item)
                is_vid = (
                    is_standalone_video(media_item)
                    or bool(media_item.get("is_video"))
                    or media_item.get("__typename") == "GraphVideo"
                )

                if filter_mode == "reels" and not is_vid:
                    continue
                if filter_mode == "photos" and is_vid:
                    continue

                for card in self._extract_media_cards(
                    media_item, fallback_username=username
                ):
                    if filter_mode == "reels":
                        card["media_type"] = "REEL"
                    elif filter_mode == "photos" and is_vid:
                        continue

                    with self._lock:
                        cid = str(card["id"])
                        if cid not in self.seen_ids:
                            self.seen_ids.add(cid)
                            self.item_found.emit(card)
                            found_count += 1

            feed_pages += 1
            more_available = bool(res_feed.get("more_available", False))
            next_feed_max = res_feed.get("next_max_id") or res_feed.get("max_id")
            self.status_message.emit(
                f"✓ [Timeline Feed] Page {feed_pages}: {len(self.seen_ids)} total items found..."
            )
            if not more_available or not next_feed_max or next_feed_max == feed_max_id:
                break
            feed_max_id = next_feed_max
            time.sleep(0.08)

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Deep profile media crawler engine: harvests Web Profile, GraphQL Timeline, and Clips stream."""
        tier_label = (
            "Reels"
            if filter_mode == "reels"
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Tier 1: Web Profile] Crawling {tier_label} for @{username}..."
        )

        # Tier 0: Harvest หน้าแรกจาก Cache
        user_data = self._profile_cache.get(username)
        if user_data:
            # 1. Timeline Grid Media (รูปภาพเดี่ยว, Carousel, และ Reels ในหน้าโปรไฟล์)
            timeline_edges = user_data.get("edge_owner_to_timeline_media", {}).get(
                "edges", []
            )
            for edge in timeline_edges:
                if self.is_cancelled:
                    return
                node = edge.get("node", {})
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

            # 2. Felix Archive (Reels)
            if filter_mode != "photos":
                felix_edges = user_data.get("edge_felix_video_timeline", {}).get(
                    "edges", []
                )
                for edge in felix_edges:
                    if self.is_cancelled:
                        return
                    node = edge.get("node", {})
                    for card in self._extract_media_cards(
                        node, fallback_username=username
                    ):
                        card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)

        # Tier 1: GraphQL Timeline Pagination (ดึงรูปภาพและ Carousel หน้าถัดไปจนหมดโปรไฟล์)
        if not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 2: GraphQL] Crawling deep timeline media for @{username}..."
            )
            self._fetch_timeline_graphql(username, user_id, filter_mode=filter_mode)

        # Tier 2: Dedicated Clips Stream (ดึง Reels ทั้งหมดจากแท็บ Reels)
        if filter_mode != "photos" and not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 3: Clips API] Crawling deep reels for @{username}..."
            )
            max_id: Optional[str] = None
            pages = 0
            while pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
                payload = {
                    "target_user_id": str(user_id),
                    "page_size": str(DEFAULT_PAGE_SIZE),
                    "include_feed_video": "true",
                    "container_module": "clips_viewer_user",
                }
                if max_id:
                    payload["max_id"] = str(max_id)

                encoded_data = urllib.parse.urlencode(payload).encode("utf-8")
                h = {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "X-IG-App-ID": IG_APP_ID,
                    "X-ASBD-ID": "129477",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{IG_BASE_URL}/{username}/reels/",
                }
                res = self._make_request(
                    IG_CLIPS_USER_URL,
                    headers=h,
                    data=encoded_data,
                    method="POST",
                    caller_tag="ClipsAPI",
                )
                if not res or not isinstance(res, dict):
                    break

                items = res.get("items") or res.get("grid_items") or []
                if not items:
                    break

                for item in items:
                    if self.is_cancelled:
                        return
                    media_item = item.get("media", item)
                    for card in self._extract_media_cards(
                        media_item, fallback_username=username
                    ):
                        card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)

                pages += 1
                paging_info = res.get("paging_info", {})
                next_max_id = paging_info.get("max_id") or res.get("next_max_id")
                more_available = bool(paging_info.get("more_available", False))

                self.status_message.emit(
                    f"✓ [Clips Stream] Page {pages}: {len(self.seen_ids)} items found so far..."
                )
                if not more_available or not next_max_id or next_max_id == max_id:
                    break
                max_id = next_max_id
                time.sleep(0.05)

    def _fetch_timeline_graphql(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user's timeline media (Photos, Carousels, Videos) via Web GraphQL Query without early pagination caps."""
        query_hash = "e769aa130647d2354c40ea6a439bfc08"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            variables: Dict[str, Any] = {"id": str(user_id), "first": 50}
            if end_cursor:
                variables["after"] = str(end_cursor)

            vars_encoded = urllib.parse.quote(json.dumps(variables))
            graphql_url = f"https://www.instagram.com/graphql/query/?query_hash={query_hash}&variables={vars_encoded}"

            h = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{IG_BASE_URL}/{username}/",
                "Accept": "*/*",
            }
            if self.cookie_str:
                h["Cookie"] = self.cookie_str
            if self._csrf_token:
                h["X-CSRFToken"] = self._csrf_token

            res = self._make_request(
                graphql_url, headers=h, caller_tag="GraphQLTimeline"
            )
            if not res or not isinstance(res, dict):
                break

            user_data = res.get("data", {}).get("user", {})
            timeline_media = user_data.get("edge_owner_to_timeline_media", {})
            edges = timeline_media.get("edges", [])
            if not edges:
                break

            for edge in edges:
                if self.is_cancelled:
                    return found_count
                node = edge.get("node", {})
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
                            found_count += 1

            page_info = timeline_media.get("page_info", {})
            has_next_page = bool(page_info.get("has_next_page", False))
            end_cursor = page_info.get("end_cursor")
            pages += 1
            self.status_message.emit(
                f"✓ [GraphQL] Page {pages}: {len(self.seen_ids)} total items found..."
            )
            time.sleep(0.08)

        return found_count

    def _fetch_timeline_graphql(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> int:
        """Paginates user's timeline media (Photos, Carousels, Videos) via Web GraphQL Query."""
        query_hash = "e769aa130647d2354c40ea6a439bfc08"
        has_next_page = True
        end_cursor: Optional[str] = None
        pages = 0
        found_count = 0

        while has_next_page and pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
            variables: Dict[str, Any] = {"id": str(user_id), "first": 50}
            if end_cursor:
                variables["after"] = str(end_cursor)

            vars_encoded = urllib.parse.quote(json.dumps(variables))
            graphql_url = f"https://www.instagram.com/graphql/query/?query_hash={query_hash}&variables={vars_encoded}"

            h = {
                "User-Agent": DEFAULT_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "X-ASBD-ID": "129477",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{IG_BASE_URL}/{username}/",
                "Accept": "*/*",
            }
            res = self._make_request(
                graphql_url, headers=h, caller_tag="GraphQLTimeline"
            )
            if not res or not isinstance(res, dict):
                break

            user_data = res.get("data", {}).get("user", {})
            timeline_media = user_data.get("edge_owner_to_timeline_media", {})
            edges = timeline_media.get("edges", [])
            if not edges:
                break

            for edge in edges:
                if self.is_cancelled:
                    return found_count
                node = edge.get("node", {})
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
                            found_count += 1

            page_info = timeline_media.get("page_info", {})
            has_next_page = bool(page_info.get("has_next_page", False))
            end_cursor = page_info.get("end_cursor")
            pages += 1
            self.status_message.emit(
                f"✓ [GraphQL] Page {pages}: {len(self.seen_ids)} total items found..."
            )
            time.sleep(0.08)

        return found_count

    def _fetch_all_profile_media_web(
        self, username: str, user_id: str, filter_mode: str = "all"
    ) -> None:
        """Deep profile media crawler engine: harvests Web Profile, Web Timeline Feed, GraphQL, and Clips stream."""
        tier_label = (
            "Reels"
            if filter_mode == "reels"
            else ("Photos" if filter_mode == "photos" else "Profile Media")
        )
        self.status_message.emit(
            f"🚀 [Tier 1: Web Profile] Crawling {tier_label} for @{username}..."
        )

        # Tier 0: Direct Web Profile Harvest (Grid Media & Felix Archive)
        user_data = self._profile_cache.get(username)
        if not user_data:
            try:
                url_profile = IG_WEB_PROFILE_INFO_URL.format(username=username)
                h_profile = {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "X-IG-App-ID": IG_APP_ID,
                    "X-ASBD-ID": "129477",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{IG_BASE_URL}/{username}/",
                }
                if self.cookie_str:
                    h_profile["Cookie"] = self.cookie_str
                if self._csrf_token:
                    h_profile["X-CSRFToken"] = self._csrf_token

                res_p = self._make_request(
                    url_profile, headers=h_profile, caller_tag="WebProfileMedia"
                )
                if res_p and isinstance(res_p, dict):
                    user_data = res_p.get("data", {}).get("user") or res_p.get("user")
            except Exception as e:
                logger.debug(f"Web profile media lookup failed: {e}")

        if user_data:
            # 1. Timeline Grid Media (Includes photos, carousels, and grid reels)
            timeline_edges = user_data.get("edge_owner_to_timeline_media", {}).get(
                "edges", []
            )
            for edge in timeline_edges:
                if self.is_cancelled:
                    return
                node = edge.get("node", {})
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

            # 2. Felix Video / Reels Archive
            if filter_mode != "photos":
                felix_edges = user_data.get("edge_felix_video_timeline", {}).get(
                    "edges", []
                )
                for edge in felix_edges:
                    if self.is_cancelled:
                        return
                    node = edge.get("node", {})
                    for card in self._extract_media_cards(
                        node, fallback_username=username
                    ):
                        card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)

        # Tier 1: Web Timeline Feed API Pagination (Photos, Carousels, Videos)
        if not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 2: Timeline Stream] Crawling feed media for @{username}..."
            )
            self._fetch_timeline_feed_web(username, filter_mode=filter_mode)

        # Tier 2: GraphQL Deep Timeline Pagination
        if not self.is_cancelled:
            self._fetch_timeline_graphql(username, user_id, filter_mode=filter_mode)

        # Tier 3: Dedicated Clips Stream (Reels Tab only)
        if filter_mode != "photos" and not self.is_cancelled:
            self.status_message.emit(
                f"🚀 [Tier 3: Clips API] Crawling deep reels for @{username}..."
            )
            max_id: Optional[str] = None
            pages = 0
            while pages < MAX_PAGINATION_PAGES and not self.is_cancelled:
                payload = {
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
                    "X-ASBD-ID": "129477",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{IG_BASE_URL}/{username}/reels/",
                }
                if self.cookie_str:
                    h["Cookie"] = self.cookie_str
                if self._csrf_token:
                    h["X-CSRFToken"] = self._csrf_token

                res = self._make_request(
                    IG_CLIPS_USER_URL,
                    headers=h,
                    data=encoded_data,
                    method="POST",
                    caller_tag="ClipsAPI",
                )
                if not res or not isinstance(res, dict):
                    params_str = urllib.parse.urlencode(payload)
                    res = self._make_request(
                        f"{IG_CLIPS_USER_URL}?{params_str}",
                        headers=h,
                        caller_tag="ClipsAPI_GET",
                    )

                if not res or not isinstance(res, dict):
                    break

                items = res.get("items") or res.get("grid_items") or []
                if not items:
                    break

                for item in items:
                    if self.is_cancelled:
                        return
                    media_item = item.get("media", item)
                    for card in self._extract_media_cards(
                        media_item, fallback_username=username
                    ):
                        card["media_type"] = "REEL"
                        with self._lock:
                            cid = str(card["id"])
                            if cid not in self.seen_ids:
                                self.seen_ids.add(cid)
                                self.item_found.emit(card)

                pages += 1
                paging_info = res.get("paging_info", {})
                next_max_id = paging_info.get("max_id") or res.get("next_max_id")
                more_available = bool(paging_info.get("more_available", False))

                self.status_message.emit(
                    f"✓ [Clips Stream] Page {pages}: {len(self.seen_ids)} items found so far..."
                )
                if not more_available or not next_max_id or next_max_id == max_id:
                    break
                max_id = next_max_id
                time.sleep(0.05)

    def _inspect_via_ytdlp(
        self, url: str, default_username: str = "", filter_mode: str = "all"
    ) -> None:
        if yt_dlp is None:
            self.error.emit("yt-dlp engine is not available.")
            return

        try:
            self.status_message.emit(
                f"⚙️ [Engine Fallback] Running deep crawler for {url}..."
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

            clean_url = normalize_url(url) or url
            if filter_mode == "reels" and default_username:
                clean_url = f"{IG_BASE_URL}/{default_username}/reels/"
            elif filter_mode != "reels":
                clean_url = re.sub(r"/(?:reels|reel)/?.*$", "/", clean_url)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                if not info or not isinstance(info, dict):
                    return

                entries = info.get("entries") or [info]
                entries = [e for e in entries if e and isinstance(e, dict)]
                total = len(entries)
                if not entries:
                    return

                is_single_post = any(
                    x in url.lower() for x in ("/p/", "/reel/", "/reels/", "/tv/")
                ) and not any(x in url.lower() for x in ("/stories/", "/highlights/"))

                code = str(
                    info.get("id")
                    or entries[0].get("id")
                    or clean_url.rstrip("/").split("/")[-1]
                )
                uploader = (
                    info.get("uploader")
                    or entries[0].get("uploader")
                    or default_username
                    or "instagram"
                )

                # Single Carousel Post Fallback
                if is_single_post and total > 1:
                    if filter_mode == "reels":
                        return
                    slides = []
                    for idx, entry in enumerate(entries, start=1):
                        has_vid = bool(
                            entry.get("video_ext")
                            or (entry.get("vcodec") and entry.get("vcodec") != "none")
                            or entry.get("ext") == "mp4"
                        )
                        direct_u = entry.get("url") or ""
                        thumb_u = entry.get("thumbnail") or ""
                        slides.append(
                            {
                                "index": idx,
                                "id": f"{code}_{idx}",
                                "is_video": has_vid,
                                "video_url": direct_u if has_vid else "",
                                "download_url": direct_u or thumb_u,
                                "thumbnail_url": thumb_u,
                            }
                        )

                    with self._lock:
                        if code in self.seen_ids:
                            return
                        self.seen_ids.add(code)

                    card = {
                        "id": code,
                        "shortcode": code,
                        "title": info.get("title")
                        or entries[0].get("title")
                        or f"Instagram Carousel #{code}",
                        "username": uploader,
                        "url": clean_url,
                        "thumbnail_url": slides[0]["thumbnail_url"] if slides else "",
                        "video_url": "",
                        "download_url": clean_url,
                        "caption": info.get("description")
                        or entries[0].get("description")
                        or "",
                        "duration": 0.0,
                        "view_count": int(info.get("view_count") or 0),
                        "like_count": int(info.get("like_count") or 0),
                        "media_type": f"CAROUSEL ({total})",
                        "carousel_count": total,
                        "slides": slides,
                        "quality": self.quality_preset,
                        "selected": True,
                        "status": "ready",
                    }
                    self.item_found.emit(card)
                    return

                # Profile list or standalone post items
                for idx, entry in enumerate(entries, start=1):
                    item_code = str(entry.get("id") or f"media_{idx}")
                    item_uploader = entry.get("uploader") or uploader
                    entry_url = entry.get("webpage_url") or entry.get("url") or ""

                    has_video = bool(
                        entry.get("video_ext")
                        or (entry.get("vcodec") and entry.get("vcodec") != "none")
                        or entry.get("ext") == "mp4"
                        or "/reel/" in entry_url.lower()
                        or "/reels/" in clean_url.lower()
                        or filter_mode == "reels"
                    )

                    badge_type = (
                        "REEL"
                        if (
                            "/reel/" in entry_url.lower()
                            or "/reels/" in clean_url.lower()
                            or filter_mode == "reels"
                            or has_video
                        )
                        else "IMAGE"
                    )

                    if (
                        filter_mode == "reels"
                        and badge_type != "REEL"
                        and not has_video
                    ):
                        continue
                    if filter_mode == "photos" and (badge_type == "REEL" or has_video):
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
                        "username": item_uploader,
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
        except Exception as ex:
            logger.debug(f"yt-dlp fallback error: {ex}")

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
            # เข้าสู่ระบบดึง Profile แบบ Multi-Tier ทันที
            self._inspect_profile(username, filter_mode=effective_mode)
        else:
            self._inspect_via_ytdlp(raw_target)

    def _fetch_stories_web(self, username: str, user_id: str) -> None:
        found_any = False
        endpoints = [
            f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/story/",
        ]
        h = {"User-Agent": MOBILE_USER_AGENT, "X-IG-App-ID": IG_APP_ID}
        if self.cookie_str:
            h["Cookie"] = self.cookie_str
        if self._csrf_token:
            h["X-CSRFToken"] = self._csrf_token

        for ep in endpoints:
            if self.is_cancelled:
                return
            res = self._make_request(ep, headers=h, caller_tag="StoriesAPI")
            if not res or not isinstance(res, dict):
                continue

            items = []
            if "reels" in res and isinstance(res["reels"], dict):
                items = (res["reels"].get(str(user_id)) or {}).get("items", [])
            elif "reels_media" in res and res["reels_media"]:
                items = res["reels_media"][0].get("items", [])
            elif "items" in res:
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
                                found_any = True
                if found_any:
                    return

        if not found_any and not self.is_cancelled:
            self._inspect_via_ytdlp(
                f"{IG_BASE_URL}/stories/{username}/", default_username=username
            )

    def run(self) -> None:
        """Parallel concurrent execution loop across input targets."""
        try:
            total = len(self.targets)
            if total == 0:
                self.finished.emit(0)
                return

            self.progress.emit(10)
            completed = 0

            workers = min(self.MAX_CONCURRENT_INSPECTS, total)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                self._executor = executor
                future_to_url = {
                    executor.submit(self._inspect_single_target, target): target
                    for target in self.targets
                }

                for future in concurrent.futures.as_completed(future_to_url):
                    if self.is_cancelled:
                        break
                    completed += 1
                    pct = int(10 + (completed / total) * 85)
                    self.progress.emit(pct)

            self.progress.emit(100)
            with self._lock:
                total_found = len(self.seen_ids)
                if total_found <= 4 and not self.cookie_str:
                    self.status_message.emit(
                        f"Done: {total_found} items found. (Tip: Import logged-in cookies to scrape beyond Instagram's 4-item public limit)."
                    )
                self.finished.emit(total_found)
        except Exception as e:
            self.error.emit(f"Inspection error: {str(e)}")
            with self._lock:
                self.finished.emit(len(self.seen_ids))
