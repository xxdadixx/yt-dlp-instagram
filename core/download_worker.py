"""
core/download_worker.py - High-speed concurrent background downloader for Instagram media items.
Features parallel workers, 128 KB socket buffer streaming, and thread-safe progress synchronization.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import ssl
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional
from PyQt6.QtCore import QThread, pyqtSignal

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

logger = logging.getLogger(__name__)

# Optimal network socket buffer (128 KB)
STREAM_BUFFER_SIZE = 128 * 1024


def sanitize_filename(name: str, max_length: int = 45) -> str:
    if not name:
        return "instagram_media"
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    cleaned = re.sub(r"[\s_]+", "_", cleaned).strip(" ._")
    return cleaned[:max_length] if cleaned else "instagram_media"


class DownloadWorker(QThread):
    progress = pyqtSignal(int)
    item_started = pyqtSignal(str)
    item_finished = pyqtSignal(str, bool)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)

    MAX_CONCURRENT_DOWNLOADS = 3

    def __init__(
        self,
        items: List[Dict[str, Any]],
        save_folder: str,
        cookie_file: Optional[str] = None,
        cookie_str: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.items = items or []
        self.save_folder = save_folder or os.path.abspath("downloads")
        self.cookie_file = cookie_file or ""
        self.cookie_str = cookie_str or ""
        self.is_cancelled = False

        self._lock = threading.Lock()
        self._completed_count = 0
        self._success_count = 0
        self._ssl_ctx = ssl._create_unverified_context()
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

    def cancel(self) -> None:
        self.is_cancelled = True
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def run(self) -> None:
        os.makedirs(self.save_folder, exist_ok=True)
        total = len(self.items)
        if total == 0:
            self.finished.emit(0)
            return

        self._completed_count = 0
        self._success_count = 0

        # Concurrent item downloads
        workers = min(self.MAX_CONCURRENT_DOWNLOADS, total)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            self._executor = executor
            future_to_item = {
                executor.submit(self._download_item_task, item, total): item
                for item in self.items
            }

            for future in concurrent.futures.as_completed(future_to_item):
                if self.is_cancelled:
                    break
                try:
                    ok = future.result()
                    with self._lock:
                        if ok:
                            self._success_count += 1
                except Exception as e:
                    logger.debug(f"Download thread error: {e}")

        with self._lock:
            self.finished.emit(self._success_count)

    def _download_item_task(self, item: Dict[str, Any], total: int) -> bool:
        if self.is_cancelled:
            return False

        card_id = str(
            item.get("card_id") or item.get("id") or item.get("shortcode") or "item"
        )
        self.item_started.emit(card_id)

        ok = self._download_item(item)
        self.item_finished.emit(card_id, ok)

        with self._lock:
            self._completed_count += 1
            pct = int((self._completed_count / total) * 100)
            self.progress.emit(pct)

        return ok

    def _download_direct_url(self, direct_url: str, output_path: str) -> bool:
        """Direct stream download with 128 KB buffer and high-throughput connection."""
        if not direct_url or not str(direct_url).startswith("http"):
            return False

        temp_path = output_path + ".part"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.instagram.com/",
            }
            if self.cookie_str:
                headers["Cookie"] = self.cookie_str

            req = urllib.request.Request(direct_url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=20) as resp:
                if resp.status == 200:
                    with open(temp_path, "wb") as f:
                        while True:
                            if self.is_cancelled:
                                self._safe_remove(temp_path)
                                return False
                            chunk = resp.read(STREAM_BUFFER_SIZE)
                            if not chunk:
                                break
                            f.write(chunk)

                    if os.path.exists(temp_path) and os.path.getsize(temp_path) > 500:
                        self._safe_remove(output_path)
                        os.replace(temp_path, output_path)
                        return True
        except Exception as e:
            logger.debug(f"Direct stream download failed for {output_path}: {e}")
            self._safe_remove(temp_path)

        return False

    def _download_via_ytdlp(self, web_url: str, username: str) -> bool:
        """Fallback yt-dlp download for whole post or carousel."""
        if not yt_dlp or not web_url:
            return False
        try:
            out_tmpl = os.path.join(self.save_folder, f"{username}_%(id)s.%(ext)s")
            ydl_opts: Dict[str, Any] = {
                "outtmpl": out_tmpl,
                "quiet": True,
                "no_warnings": True,
                "format": "bestvideo+bestaudio/best",
                "concurrent_fragment_downloads": 4,
            }
            if self.cookie_file and os.path.exists(self.cookie_file):
                ydl_opts["cookiefile"] = self.cookie_file

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([web_url])
            return True
        except Exception as e:
            logger.error(f"yt-dlp fallback failed for {web_url}: {e}")
            return False

    def _download_item(self, item: Dict[str, Any]) -> bool:
        slides = item.get("slides")
        uname = sanitize_filename(item.get("username") or "instagram", 30)
        shortcode = item.get("shortcode") or item.get("id") or str(int(time.time()))
        target_web_url = item.get("url") or ""

        # Case A: Multi-Slide Carousel Post
        if slides and isinstance(slides, list):
            all_ok = True
            for slide in slides:
                if self.is_cancelled:
                    return False
                idx = slide.get("index", 1)
                is_vid = bool(slide.get("is_video"))
                ext = ".mp4" if is_vid else ".jpg"
                target_path = os.path.join(
                    self.save_folder, f"{uname}_{shortcode}_{idx}{ext}"
                )

                direct_url = (
                    slide.get("download_url")
                    or slide.get("video_url")
                    or slide.get("thumbnail_url")
                )
                slide_ok = self._download_direct_url(direct_url, target_path)
                if not slide_ok:
                    all_ok = False

            if all_ok:
                return True
            return self._download_via_ytdlp(target_web_url, uname)

        # Case B: Single Video / Photo Post
        media_type = str(item.get("media_type") or item.get("type") or "POST").upper()
        is_video = (
            "VIDEO" in media_type
            or "REEL" in media_type
            or ("STORY" in media_type and bool(item.get("video_url")))
        )
        video_url = item.get("video_url")
        download_url = item.get("download_url") or item.get("thumbnail_url")
        ext = ".mp4" if is_video else ".jpg"
        target_path = os.path.join(self.save_folder, f"{uname}_{shortcode}{ext}")

        direct_stream = video_url if is_video else download_url
        if (
            direct_stream
            and str(direct_stream).startswith("http")
            and "/p/" not in direct_stream
            and "/reel/" not in direct_stream
        ):
            if self._download_direct_url(direct_stream, target_path):
                return True

        return self._download_via_ytdlp(target_web_url, uname)

    def _safe_remove(self, path: str) -> None:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
