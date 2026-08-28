"""
core/download_worker.py - Resilient background download worker for Instagram media items.
Supports direct CDN stream downloads, API media info extraction, and yt-dlp fallback.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
import re
from typing import Any, Dict, List, Optional

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
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_USER_AGENT,
    IG_APP_ID,
    IG_BASE_URL,
    MOBILE_USER_AGENT,
)
from core.parser import shortcode_to_id

logger = logging.getLogger(__name__)


class DownloadWorker(QThread):
    """
    Background worker thread to download queued Instagram media items.
    Emits granular progress, item status signals, and final summary counts.
    """

    progress = pyqtSignal(int)
    item_started = pyqtSignal(str)
    item_finished = pyqtSignal(str, bool)
    status_message = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(
        self,
        items: List[Dict[str, Any]],
        save_folder: str,
        cookie_str: Optional[str] = None,
        cookie_file: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.items: List[Dict[str, Any]] = items or []
        self.save_folder: str = save_folder or os.path.abspath("downloads")
        self.cookie_str: str = (cookie_str or "").strip()
        self.cookie_file: str = (cookie_file or "").strip()
        self.is_cancelled: bool = False
        self._ssl_ctx = ssl._create_unverified_context()

    def cancel(self) -> None:
        self.is_cancelled = True

    def run(self) -> None:
        os.makedirs(self.save_folder, exist_ok=True)
        total = len(self.items)
        if total == 0:
            self.finished.emit(0)
            return

        success_count = 0
        for idx, item in enumerate(self.items):
            if self.is_cancelled:
                break

            shortcode = str(item.get("shortcode") or item.get("id") or f"media_{idx}")
            username = str(item.get("username") or "instagram")
            url = item.get("url") or f"{IG_BASE_URL}/reel/{shortcode}/"

            self.item_started.emit(shortcode)
            self.status_message.emit(
                f"Downloading ({idx + 1}/{total}): @{username} [{shortcode}]..."
            )

            success = self._download_single(item, url, shortcode, username)
            if success:
                success_count += 1

            self.item_finished.emit(shortcode, success)
            pct = int((idx + 1) / total * 100)
            self.progress.emit(pct)

        self.finished.emit(success_count)

    def _download_stream_url(self, direct_url: str, output_path: str) -> bool:
        """Streams media directly from CDN URL with header fallbacks and atomic file write."""
        if not direct_url or not direct_url.startswith("http"):
            return False

        headers_list = [
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "*/*",
                "Referer": "https://www.instagram.com/",
            },
            {"User-Agent": MOBILE_USER_AGENT, "Accept": "*/*"},
            {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"},
        ]
        if self.cookie_str:
            for h in headers_list:
                h["Cookie"] = self.cookie_str

        temp_path = output_path + ".part"
        for headers in headers_list:
            if self.is_cancelled:
                return False
            try:
                req = urllib.request.Request(direct_url, headers=headers)
                with urllib.request.urlopen(
                    req, context=self._ssl_ctx, timeout=DEFAULT_REQUEST_TIMEOUT
                ) as resp:
                    if hasattr(resp, "status") and resp.status not in (200, 206):
                        continue
                    with open(temp_path, "wb") as out_f:
                        while True:
                            if self.is_cancelled:
                                break
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out_f.write(chunk)

                if self.is_cancelled:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    return False

                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass
                    os.replace(temp_path, output_path)
                    return True
            except Exception as e:
                logger.debug(f"Direct stream download failed for {direct_url}: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                continue

        return False

    def _fetch_direct_video_url(self, shortcode: str) -> Optional[str]:
        """Attempts to resolve direct CDN MP4 URL via mobile media info API."""
        try:
            media_id = shortcode_to_id(shortcode)
            if not media_id:
                return None
            info_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
            headers = {
                "User-Agent": MOBILE_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
            }
            if self.cookie_str:
                headers["Cookie"] = self.cookie_str
            req = urllib.request.Request(info_url, headers=headers)
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=DEFAULT_REQUEST_TIMEOUT
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                items = data.get("items", [])
                if items:
                    v_versions = items[0].get("video_versions", [])
                    if v_versions and isinstance(v_versions, list):
                        return v_versions[0].get("url")
        except Exception:
            pass
        return None

    def _download_single(
        self, item: Dict[str, Any], url: str, shortcode: str, username: str
    ) -> bool:
        """Attempts direct CDN download first, then API lookup, then yt-dlp fallback."""
        clean_user = self.sanitize_filename(username)
        clean_sc = self.sanitize_filename(shortcode)
        filename = f"{clean_user}_{clean_sc}.mp4"
        out_path = os.path.join(self.save_folder, filename)

        # 1. Direct pre-extracted video URL
        direct_url = item.get("video_url") or item.get("download_url")
        if direct_url and direct_url.startswith("http"):
            if self._download_stream_url(direct_url, out_path):
                return True

        # 2. Resolve direct CDN URL from media info API
        if shortcode:
            api_direct_url = self._fetch_direct_video_url(shortcode)
            if api_direct_url:
                if self._download_stream_url(api_direct_url, out_path):
                    return True

        # 3. Fallback: Download via yt-dlp
        if yt_dlp is not None:
            try:
                media_type = item.get("media_type", "reel")
                canonical_url = (
                    f"{IG_BASE_URL}/p/{clean_sc}/"
                    if media_type == "post"
                    else f"{IG_BASE_URL}/reel/{clean_sc}/"
                )
                out_tmpl = os.path.join(
                    self.save_folder,
                    f"{clean_user}_{clean_sc}.%(ext)s",
                )
                ydl_opts = {
                    "outtmpl": out_tmpl,
                    "quiet": True,
                    "no_warnings": True,
                    "ignoreerrors": True,
                    "windowsfilenames": True,
                    "restrictfilenames": True,
                    "trim_file_name": 60,
                    "socket_timeout": DEFAULT_REQUEST_TIMEOUT,
                    "retries": 3,
                    "fragment_retries": 3,
                    "nocheckcertificate": True,
                    "http_headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": "https://www.instagram.com/",
                    },
                }
                if self.cookie_file and os.path.exists(self.cookie_file):
                    ydl_opts["cookiefile"] = self.cookie_file

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ret = ydl.download([canonical_url])
                    if ret == 0 and os.path.exists(out_path):
                        return True
                    if direct_url and direct_url.startswith("http"):
                        ret2 = ydl.download([direct_url])
                        if ret2 == 0 and os.path.exists(out_path):
                            return True
            except Exception as ex:
                logger.debug(f"yt-dlp download failed for {url}: {ex}")

        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    @staticmethod
    def sanitize_filename(name: str, max_length: int = 40) -> str:
        """Sanitizes strings to be safe for file paths across Windows, macOS, and Linux."""
        if not name:
            return "instagram_media"
        cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
        cleaned = re.sub(r"[\s_]+", "_", cleaned)
        cleaned = cleaned.strip(" ._")
        return cleaned[:max_length] if cleaned else "instagram_media"
