"""
core/download_worker.py - High-throughput sequential media download worker
with monotonic chronological ordering (Most Recent -> Oldest), connection pooling,
256 KB streaming buffers, and atomic file replacement.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Union

import requests
from PyQt6.QtCore import QThread, pyqtSignal
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from config.constants import DEFAULT_USER_AGENT, IG_APP_ID
from core.parser import shortcode_to_id
from utils.file_utils import sanitize_filesystem_name

logger = logging.getLogger("DownloadWorker")


def sanitize_filename(value: str, fallback: str = "media") -> str:
    """Sanitizes filename strings for safe filesystem operations."""
    cleaned = sanitize_filesystem_name(str(value), max_length=64)
    return cleaned if cleaned else fallback


def extract_chronological_key(item_data: Dict[str, Any]) -> int:
    """Extracts monotonic integer timestamp or snowflake ID from media payload (higher = newer)."""
    if not isinstance(item_data, dict):
        return 0

    ts = (
        item_data.get("taken_at_timestamp")
        or item_data.get("taken_at")
        or item_data.get("timestamp")
        or item_data.get("date")
    )
    if ts:
        try:
            return int(ts)
        except (ValueError, TypeError):
            pass

    raw_id = item_data.get("id") or item_data.get("pk") or item_data.get("media_id")
    if raw_id:
        try:
            clean_id = str(raw_id).split("_")[0]
            val = int(clean_id)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

    shortcode = item_data.get("shortcode") or item_data.get("code")
    if shortcode and isinstance(shortcode, str):
        decoded = shortcode_to_id(shortcode)
        if decoded is not None and decoded > 0:
            return decoded

    return 0


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    item_started = pyqtSignal(str)
    item_finished = pyqtSignal(str, bool)
    finished = pyqtSignal(int)

    sanitize_filename = staticmethod(sanitize_filename)

    def __init__(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        save_folder: str = "downloads",
        cookie_file: Optional[str] = None,
        cookie_str: Optional[str] = None,
        quality_preset: str = "best_video",
        queue_items: Optional[List[Dict[str, Any]]] = None,
        output_directory: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        raw_items: List[Dict[str, Any]] = list(items or queue_items or [])

        # Strict Chronological Sort: Most Recent (highest timestamp/ID) -> Oldest (lowest)
        raw_items.sort(key=extract_chronological_key, reverse=True)
        self.items: List[Dict[str, Any]] = raw_items

        self.save_folder: str = os.path.abspath(
            save_folder or output_directory or "downloads"
        )
        self.cookie_file: Optional[str] = (
            cookie_file if cookie_file and os.path.exists(cookie_file) else None
        )
        self.cookie_str: Optional[str] = cookie_str
        self.quality_preset: str = quality_preset
        self._is_cancelled: bool = False
        os.makedirs(self.save_folder, exist_ok=True)

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
            "X-IG-App-ID": IG_APP_ID,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        self.session.headers.update(headers)

    def cancel(self) -> None:
        """Signals the worker to abort active downloads cooperatively."""
        self._is_cancelled = True
        self.requestInterruption()

    def _build_filename(
        self,
        item_or_username: Union[Dict[str, Any], str],
        shortcode: Optional[str] = None,
        ext: str = "mp4",
        slide_index: Optional[int] = None,
    ) -> str:
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

        clean_user = sanitize_filename(str(raw_user), fallback="instagram")
        clean_code = sanitize_filename(str(raw_code), fallback="media")
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
        filename = self._build_filename(
            item_or_username=item_or_username,
            shortcode=shortcode,
            ext=ext,
            slide_index=slide_index,
        )
        return os.path.join(self.save_folder, filename)

    def _get_download_headers(self, url: str) -> Dict[str, str]:
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

    def _download_direct_stream(
        self, url: str, target_path: str, index: int, total_items: int
    ) -> str:
        chunk_size = 256 * 1024  # 256 KB streaming chunks
        temp_path = f"{target_path}.part"
        req_headers = self._get_download_headers(url)
        completed_successfully = False

        try:
            with self.session.get(
                url,
                headers=req_headers,
                stream=True,
                timeout=(6.0, 30.0),
                verify=True,
            ) as response:
                response.raise_for_status()
                try:
                    total_size = int(response.headers.get("content-length", 0) or 0)
                except (ValueError, TypeError):
                    total_size = 0

                downloaded_bytes = 0
                last_signal_time = 0.0

                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if self._is_cancelled or self.isInterruptionRequested():
                            raise InterruptedError("Download cancelled by user.")

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

            if self._is_cancelled or self.isInterruptionRequested():
                raise InterruptedError("Download cancelled by user.")

            os.replace(temp_path, target_path)
            completed_successfully = True
            return target_path

        except (requests.exceptions.RequestException, OSError, InterruptedError) as exc:
            if not self._is_cancelled:
                logger.warning("Stream failed for target %s: %s", target_path, exc)
            return ""

        finally:
            if not completed_successfully and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _download_via_ytdlp(
        self,
        item: Dict[str, Any],
        username: str,
        shortcode: str,
        index: int,
        total_items: int,
    ) -> str:
        if yt_dlp is None or self._is_cancelled or self.isInterruptionRequested():
            return ""

        url = str(
            item.get("url")
            or item.get("webpage_url")
            or item.get("download_url")
            or f"https://www.instagram.com/p/{shortcode}/"
        )
        clean_user = sanitize_filename(username, fallback="instagram_user")
        clean_code = sanitize_filename(shortcode, fallback="media")
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

        def ytdlp_hook(d: Dict[str, Any]) -> None:
            nonlocal last_progress_time
            if self._is_cancelled or self.isInterruptionRequested():
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

        ydl_opts: Dict[str, Any] = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [ytdlp_hook],
            "nocheckcertificate": False,
            "buffersize": 256 * 1024,
            "http_chunk_size": 10485760,
            "concurrent_fragment_downloads": 4,
            "retries": 3,
            "fragment_retries": 3,
        }

        from utils.file_utils import get_ffmpeg_dir

        ffmpeg_bin_dir = get_ffmpeg_dir()
        if ffmpeg_bin_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin_dir

        if self.quality_preset == "audio_only":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        elif self.quality_preset == "720p":
            ydl_opts["format"] = (
                "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
            )
        elif self.quality_preset == "1080p":
            ydl_opts["format"] = (
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
            )

        if self.cookie_file and os.path.exists(self.cookie_file):
            ydl_opts["cookiefile"] = self.cookie_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    return ""
                if "entries" in info and info["entries"]:
                    return ydl.prepare_filename(info["entries"][0])
                return ydl.prepare_filename(info)
        except Exception as exc:
            if not self._is_cancelled:
                logger.warning("yt-dlp download failed for %s: %s", url, exc)
            return ""

    def _process_single_item(
        self, item: Dict[str, Any], index: int, total_items: int
    ) -> str:
        if self._is_cancelled:
            return ""

        username = str(item.get("username") or item.get("uploader") or "instagram_user")
        shortcode = str(item.get("shortcode") or item.get("id") or f"media_{index}")

        # 1. Multi-Item Carousel -> Direct Stream Each Slide
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
                    item_or_username=username,
                    shortcode=shortcode,
                    slide_index=s_idx,
                    ext=ext,
                )

                if slide_url and (
                    slide_url.startswith("http://") or slide_url.startswith("https://")
                ):
                    try:
                        out = self._download_direct_stream(
                            slide_url, slide_path, index, total_items
                        )
                        if out and os.path.isfile(out) and os.path.getsize(out) > 0:
                            saved_any = True
                            last_path = out
                    except Exception as s_err:
                        if self._is_cancelled:
                            break
                        logger.debug("Slide %d direct stream failed: %s", s_idx, s_err)

            if self._is_cancelled:
                return ""
            if saved_any:
                return last_path
            return self._download_via_ytdlp(
                item, username, shortcode, index, total_items
            )

        # 2. Single Post / Reel / Image -> Direct Stream
        direct_url = (
            item.get("download_url") or item.get("media_url") or item.get("video_url")
        )
        media_type = str(item.get("type") or item.get("media_type") or "video").lower()
        is_video = "image" not in media_type and "photo" not in media_type
        ext = (
            "mp3"
            if self.quality_preset == "audio_only"
            else ("mp4" if is_video else "jpg")
        )
        target_path = self._build_filepath(
            item_or_username=username,
            shortcode=shortcode,
            ext=ext,
        )

        is_direct_cdn = bool(
            self.quality_preset != "audio_only"
            and direct_url
            and direct_url.startswith("http")
            and not any(
                x in direct_url
                for x in ("/p/", "/reel/", "/reels/", "/tv/", "/stories/")
            )
        )

        if is_direct_cdn:
            try:
                stream_res = self._download_direct_stream(
                    direct_url, target_path, index, total_items
                )
                if (
                    stream_res
                    and os.path.isfile(stream_res)
                    and os.path.getsize(stream_res) > 0
                ):
                    return stream_res
            except Exception as stream_err:
                if self._is_cancelled:
                    return ""
                logger.warning(
                    "Direct stream exception for %s_%s: %s",
                    username,
                    shortcode,
                    stream_err,
                )

        # 3. yt-dlp Scrape Fallback (executed if direct stream was skipped or produced no output)
        return self._download_via_ytdlp(item, username, shortcode, index, total_items)

    def run(self) -> None:
        total_items = len(self.items)
        if total_items == 0:
            self.progress.emit(100)
            self.finished.emit(0)
            return

        success_count = 0
        logger.info(
            "Starting sequential download queue in chronological order (%d items)",
            total_items,
        )

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

                is_valid = bool(
                    saved_path
                    and os.path.isfile(saved_path)
                    and os.path.getsize(saved_path) > 0
                )

                if is_valid:
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
