# core/download_worker.py
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Union
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from PyQt6.QtCore import QThread, pyqtSignal

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from config.constants import DEFAULT_USER_AGENT
except (ImportError, AttributeError):
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

try:
    from config.constants import INSTAGRAM_APP_ID
except (ImportError, AttributeError):
    try:
        from config.constants import IG_APP_ID as INSTAGRAM_APP_ID
    except (ImportError, AttributeError):
        INSTAGRAM_APP_ID = "936619743392459"

logger = logging.getLogger("DownloadWorker")


class DownloadWorker(QThread):
    """Sequential media download worker with persistent socket connection pools,

    256 KB streaming buffers, and strict username_shortcode filename formatting.
    """

    progress = pyqtSignal(int)
    item_started = pyqtSignal(str)
    item_finished = pyqtSignal(str, bool)
    finished = pyqtSignal(int)

    def __init__(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        save_folder: str = "downloads",
        cookie_file: Optional[str] = None,
        cookie_str: Optional[str] = None,
        queue_items: Optional[List[Dict[str, Any]]] = None,
        output_directory: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.items: List[Dict[str, Any]] = items or queue_items or []
        self.save_folder: str = os.path.abspath(
            save_folder or output_directory or "downloads"
        )
        self.cookie_file: Optional[str] = (
            cookie_file if cookie_file and os.path.exists(cookie_file) else None
        )
        self.cookie_str: Optional[str] = cookie_str
        self._is_cancelled: bool = False
        os.makedirs(self.save_folder, exist_ok=True)

        # Connection-pooled HTTP session for high-throughput streaming
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            pool_connections=16,
            pool_maxsize=16,
            max_retries=retries,
            pool_block=False,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "X-IG-App-ID": INSTAGRAM_APP_ID,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        self.session.headers.update(headers)

    def cancel(self) -> None:
        """Signals the worker to abort active downloads immediately."""
        self._is_cancelled = True
        try:
            self.session.close()
        except Exception:
            pass

    @staticmethod
    def _sanitize_name(value: str, fallback: str = "media") -> str:
        """Strips invalid filesystem characters, emojis, and whitespace."""
        cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]', "", str(value)).strip()
        return cleaned or fallback

    def _build_filename(
        self,
        item_or_username: Union[Dict[str, Any], str],
        shortcode: Optional[str] = None,
        ext: str = "mp4",
        slide_index: Optional[int] = None,
    ) -> str:
        """
        Constructs a sanitized filename adhering strictly to:
        {username}_{shortcode}[_{slide_index}].{ext}

        Accepts either an item dictionary or positional strings.
        """
        from utils.file_utils import sanitize_filesystem_name

        if isinstance(item_or_username, dict):
            raw_user = item_or_username.get("username") or "instagram"
            raw_code = (
                item_or_username.get("shortcode")
                or item_or_username.get("id")
                or "media"
            )
            slide_idx = item_or_username.get("slide_index") or item_or_username.get(
                "index"
            )
        else:
            raw_user = item_or_username or "instagram"
            raw_code = shortcode or "media"
            slide_idx = slide_index

        clean_user = sanitize_filesystem_name(str(raw_user), max_length=64)
        clean_code = sanitize_filesystem_name(str(raw_code), max_length=64)
        clean_ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower() or "mp4"

        if slide_idx is not None:
            return f"{clean_user}_{clean_code}_{slide_idx}.{clean_ext}"
        return f"{clean_user}_{clean_code}.{clean_ext}"

    def _build_filepath(
        self,
        item_or_username: Union[Dict[str, Any], str],
        shortcode: Optional[str] = None,
        ext: str = "mp4",
        slide_index: Optional[int] = None,
    ) -> str:
        """
        Resolves the absolute destination file path within self.save_folder.
        """
        filename = self._build_filename(
            item_or_username=item_or_username,
            shortcode=shortcode,
            ext=ext,
            slide_index=slide_index,
        )
        return os.path.join(self.save_folder, filename)

    def _get_download_headers(self, url: str) -> Dict[str, str]:
        """Returns minimal headers for CDN assets, omitting session cookies to protect account."""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }
        is_cdn = any(domain in url for domain in ("cdninstagram.com", "fbcdn.net"))
        if not is_cdn and self.cookie_str:
            headers["Cookie"] = self.cookie_str
        return headers

    def sanitize_filename(value: str, fallback: str = "media") -> str:
        """Sanitizes filename strings for safe OS filesystem writes."""
        from utils.file_utils import sanitize_filesystem_name

        cleaned = sanitize_filesystem_name(str(value), max_length=64)
        return cleaned if cleaned else fallback

    def _download_direct_stream(
        self, url: str, target_path: str, index: int, total_items: int
    ) -> str:
        """Streams media directly via 256 KB chunk buffering with smooth progress reporting."""
        CHUNK_SIZE = 256 * 1024
        temp_path = f"{target_path}.part"
        req_headers = self._get_download_headers(url)

        with self.session.get(
            url, headers=req_headers, stream=True, timeout=(6.0, 30.0)
        ) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            downloaded_bytes = 0
            last_signal_time = 0.0

            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if self._is_cancelled:
                        f.close()
                        if os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
                        raise InterruptedError("Download cancelled.")

                    if chunk:
                        f.write(chunk)
                        downloaded_bytes += len(chunk)

                        current_time = time.time()
                        if (
                            current_time - last_signal_time >= 0.05
                            or downloaded_bytes == total_size
                        ):
                            last_signal_time = current_time
                            item_percent = (
                                (downloaded_bytes / total_size)
                                if total_size > 0
                                else 0.0
                            )
                            overall_percent = int(
                                ((index + item_percent) / total_items) * 100
                            )
                            self.progress.emit(min(overall_percent, 99))

        if self._is_cancelled:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise InterruptedError("Download cancelled.")

        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass
        os.rename(temp_path, target_path)
        return target_path

    def _download_via_ytdlp(
        self,
        item: Dict[str, Any],
        username: str,
        shortcode: str,
        index: int,
        total_items: int,
    ) -> str:
        """Executes yt-dlp fallback with strict username_shortcode outtmpl (no captions)."""
        if yt_dlp is None or self._is_cancelled:
            return ""

        url = str(
            item.get("url")
            or item.get("webpage_url")
            or item.get("download_url")
            or f"https://www.instagram.com/p/{shortcode}/"
        )
        clean_user = self._sanitize_name(username, fallback="instagram_user")
        clean_code = self._sanitize_name(shortcode, fallback="media")
        is_carousel = "carousel" in str(item.get("media_type", "")).lower() or bool(
            item.get("carousel_count")
        )

        if is_carousel:
            out_template = os.path.join(
                self.save_folder,
                f"{clean_user}_{clean_code}_%(playlist_index)s.%(ext)s",
            )
        else:
            out_template = os.path.join(
                self.save_folder, f"{clean_user}_{clean_code}.%(ext)s"
            )

        last_progress_time = 0.0

        def ytdlp_hook(d):
            nonlocal last_progress_time
            if self._is_cancelled:
                raise yt_dlp.utils.DownloadCancelled("Download cancelled by user.")

            if d.get("status") == "downloading":
                now = time.time()
                if now - last_progress_time >= 0.05:
                    last_progress_time = now
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    item_percent = (downloaded / total) if total > 0 else 0.0
                    overall_percent = int(((index + item_percent) / total_items) * 100)
                    self.progress.emit(min(overall_percent, 99))

        ydl_opts = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [ytdlp_hook],
            "nocheckcertificate": True,
            "buffersize": 256 * 1024,
            "http_chunk_size": 10485760,
            "concurrent_fragment_downloads": 4,
            "retries": 3,
            "fragment_retries": 3,
        }

        if self.cookie_file and os.path.exists(self.cookie_file):
            ydl_opts["cookiefile"] = self.cookie_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return ""
            if "entries" in info and info["entries"]:
                return ydl.prepare_filename(info["entries"][0])
            return ydl.prepare_filename(info)

    def _process_single_item(
        self, item: Dict[str, Any], index: int, total_items: int
    ) -> str:
        """Downloads single media or carousels using strict username_shortcode formatting."""
        if self._is_cancelled:
            return ""

        username = str(item.get("username") or item.get("uploader") or "instagram_user")
        shortcode = str(item.get("shortcode") or item.get("id") or f"media_{index}")

        # Strategy 1: Multi-Item Carousel -> Direct Stream Each Slide
        slides = item.get("slides")
        if slides and isinstance(slides, list) and len(slides) > 0:
            saved_any = False
            last_path = ""
            for s_idx, slide in enumerate(slides, start=1):
                if self._is_cancelled:
                    break
                slide_url = (
                    slide.get("download_url")
                    or slide.get("video_url")
                    or slide.get("thumbnail_url")
                )
                is_vid = bool(slide.get("is_video"))
                ext = "mp4" if is_vid else "jpg"
                slide_path = self._build_filepath(
                    username, shortcode, slide_index=s_idx, ext=ext
                )

                if slide_url and (
                    slide_url.startswith("http://") or slide_url.startswith("https://")
                ):
                    try:
                        self._download_direct_stream(
                            slide_url, slide_path, index, total_items
                        )
                        if os.path.exists(slide_path):
                            saved_any = True
                            last_path = slide_path
                    except Exception as s_err:
                        if self._is_cancelled:
                            break
                        logger.debug("Slide %d direct stream failed: %s", s_idx, s_err)

            if self._is_cancelled:
                return ""
            if saved_any:
                return last_path
            # Fallback to yt-dlp for carousel
            return self._download_via_ytdlp(
                item, username, shortcode, index, total_items
            )

        # Strategy 2: Single Post / Reel / Image -> Direct Stream
        direct_url = (
            item.get("download_url") or item.get("media_url") or item.get("video_url")
        )
        media_type = str(item.get("type") or item.get("media_type") or "video").lower()
        is_video = "image" not in media_type and "photo" not in media_type
        ext = "mp4" if is_video else "jpg"
        target_path = self._build_filepath(username, shortcode, ext=ext)

        is_direct_cdn = bool(
            direct_url
            and direct_url.startswith("http")
            and not any(
                x in direct_url
                for x in ("/p/", "/reel/", "/reels/", "/tv/", "/stories/")
            )
        )

        if is_direct_cdn:
            try:
                return self._download_direct_stream(
                    direct_url, target_path, index, total_items
                )
            except Exception as stream_err:
                if self._is_cancelled:
                    return ""
                logger.warning(
                    "Direct stream failed for %s_%s, falling back to yt-dlp: %s",
                    username,
                    shortcode,
                    stream_err,
                )

        # Strategy 3: yt-dlp Fallback
        return self._download_via_ytdlp(item, username, shortcode, index, total_items)

    def run(self) -> None:
        """Sequential queue execution loop."""
        total_items = len(self.items)
        if total_items == 0:
            self.progress.emit(100)
            self.finished.emit(0)
            return

        success_count = 0
        logger.info("Starting sequential download queue (%d items)", total_items)

        for index, item in enumerate(self.items):
            if self._is_cancelled:
                logger.warning("Download queue cancelled by user.")
                break

            item_id = str(
                item.get("id") or item.get("shortcode") or item.get("url") or index
            )
            self.item_started.emit(item_id)

            try:
                saved_path = self._process_single_item(item, index, total_items)
                if self._is_cancelled:
                    self.item_finished.emit(item_id, False)
                    break

                # Ensure target file actually exists on filesystem and is non-empty
                is_valid_download = bool(
                    saved_path
                    and os.path.isfile(saved_path)
                    and os.path.getsize(saved_path) > 0
                )

                if is_valid_download:
                    success_count += 1
                    self.item_finished.emit(item_id, True)
                else:
                    self.item_finished.emit(item_id, False)
            except Exception as exc:
                if self._is_cancelled:
                    self.item_finished.emit(item_id, False)
                    break
                logger.error(
                    "Error downloading item %s: %s", item_id, exc, exc_info=True
                )
                self.item_finished.emit(item_id, False)

            curr_prog = int(((index + 1) / total_items) * 100)
            self.progress.emit(curr_prog)

        self.progress.emit(100)
        self.finished.emit(success_count)
