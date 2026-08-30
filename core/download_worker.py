import os
import re
import time
import math
import logging
import requests
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from typing import List, Dict, Any, Optional
from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp
from utils.file_utils import sanitize_filename

# Fallback-safe constant resolution
try:
    from config.constants import DEFAULT_USER_AGENT
except (ImportError, AttributeError):
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
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
    """
    High-Performance Sequential Download Worker with Abort / Cancel capabilities.
    Processes media items strictly 1-by-1 to prevent race conditions and skipped items,
    utilizing persistent socket connection pools and 256 KB streaming buffers.
    """

    # Signals expected by MainWindow
    progress = pyqtSignal(int)  # Overall queue progress percent (0-100)
    item_started = pyqtSignal(str)  # item_id
    item_finished = pyqtSignal(str, bool)  # item_id, is_success
    finished = pyqtSignal(int)  # success_count

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
        self.save_folder: str = save_folder or output_directory or "downloads"
        self.cookie_file: Optional[str] = cookie_file
        self.cookie_str: Optional[str] = cookie_str
        self._is_cancelled: bool = False

        # Configure high-throughput HTTP session with connection pooling and automated retries
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
        """Signals the worker to abort current and future downloads immediately."""
        self._is_cancelled = True
        try:
            self.session.close()
        except Exception:
            pass

    def run(self) -> None:
        """Main sequential execution loop."""
        total_items = len(self.items)
        if total_items == 0:
            self.progress.emit(100)
            self.finished.emit(0)
            return

        success_count = 0
        logger.info(f"Starting sequential download queue ({total_items} items)")

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

                if saved_path and (
                    os.path.exists(saved_path) or os.path.isdir(self.save_folder)
                ):
                    success_count += 1
                    self.item_finished.emit(item_id, True)
                else:
                    self.item_finished.emit(item_id, False)
            except Exception as exc:
                if self._is_cancelled:
                    self.item_finished.emit(item_id, False)
                    break
                logger.error(f"Error downloading item {item_id}: {exc}", exc_info=True)
                self.item_finished.emit(item_id, False)

            # Update overall progress step
            curr_prog = int(((index + 1) / total_items) * 100)
            self.progress.emit(curr_prog)

        self.progress.emit(100)
        self.finished.emit(success_count)

    def _process_single_item(
        self, item: Dict[str, Any], index: int, total_items: int
    ) -> str:
        """Determines best download strategy (Carousel child downloader, Direct CDN streaming, or yt-dlp)."""
        if self._is_cancelled:
            return ""

        title = item.get("title") or item.get("id") or f"instagram_{int(time.time())}"
        clean_title = sanitize_filename(title)[:80]
        item_id = str(item.get("id") or item.get("shortcode") or index)

        # Strategy 1: Multi-Item Carousel Post -> Download all slide images/videos
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
                ext = ".mp4" if is_vid else ".jpg"
                slide_path = os.path.join(
                    self.save_folder, f"{clean_title}_{item_id}_{s_idx}{ext}"
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
                        logger.debug(f"Slide {s_idx} stream failed: {s_err}")

            if self._is_cancelled:
                return ""
            if saved_any:
                return last_path
            # Fallback to yt-dlp if direct slide streaming failed
            return self._download_via_ytdlp(item, clean_title, index, total_items)

        # Strategy 2: Single Post / Reel / Image
        direct_url = (
            item.get("download_url") or item.get("media_url") or item.get("video_url")
        )
        media_type = (item.get("type") or item.get("media_type") or "video").lower()

        ext = (
            ".mp4"
            if "image" not in media_type and "photo" not in media_type
            else ".jpg"
        )
        target_path = os.path.join(self.save_folder, f"{clean_title}_{item_id}{ext}")

        # Verify that direct_url is an actual CDN media link and not an Instagram HTML page
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
                    f"Direct stream failed for {item_id}, falling back to yt-dlp: {stream_err}"
                )

        # Strategy 3: yt-dlp Fallback
        return self._download_via_ytdlp(item, clean_title, index, total_items)

    def _get_download_headers(self, url: str) -> dict[str, str]:
        """Returns minimal headers for CDN assets, omitting session cookies to protect account."""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }
        # Only attach session cookies when requesting instagram.com API endpoints, never CDNs
        is_cdn = any(domain in url for domain in ("cdninstagram.com", "fbcdn.net"))
        if not is_cdn and self.cookie_str:
            headers["Cookie"] = self.cookie_str
        return headers

    def _download_direct_stream(
        self, url: str, target_path: str, index: int, total_items: int
    ) -> str:
        """Streams media directly via 256 KB chunk buffering with smooth overall progress updates."""
        CHUNK_SIZE = 256 * 1024  # 256 KB buffer
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

                        # Throttle progress calculation to max 20Hz (every 50ms)
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
        self, item: Dict[str, Any], clean_title: str, index: int, total_items: int
    ) -> str:
        """Executes yt-dlp extractor fallback with progress hook."""
        if self._is_cancelled:
            return ""

        url = item.get("url") or item.get("webpage_url") or item.get("download_url")
        outtmpl = os.path.join(self.save_folder, f"{clean_title}_%(id)s.%(ext)s")
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
            "outtmpl": outtmpl,
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
