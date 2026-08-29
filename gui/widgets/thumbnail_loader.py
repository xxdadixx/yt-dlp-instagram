"""
gui/widgets/thumbnail_loader.py - Asynchronous Instagram thumbnail downloader.
Handles Instagram CDN headers, SSL certificate tolerance, and in-memory caching.
"""

from __future__ import annotations

import logging
import ssl
import urllib.request
from typing import Callable, Dict, Optional
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)

# In-memory cache for fetched thumbnail bytes
_THUMBNAIL_BYTE_CACHE: Dict[str, bytes] = {}


class ThumbnailLoader(QThread):
    """
    Background worker thread to fetch Instagram thumbnail bytes
    with headers matching Instagram's CDN expectations.
    """

    loaded = pyqtSignal(bytes)

    def __init__(self, url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.url = (url or "").strip()
        self.is_cancelled = False
        self._ssl_ctx = ssl._create_unverified_context()

    def cancel(self) -> None:
        self.is_cancelled = True

    def run(self) -> None:
        if not self.url or not self.url.startswith("http"):
            return

        # Check in-memory cache first
        if self.url in _THUMBNAIL_BYTE_CACHE:
            if not self.is_cancelled:
                self.loaded.emit(_THUMBNAIL_BYTE_CACHE[self.url])
            return

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.instagram.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        try:
            req = urllib.request.Request(self.url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
                if resp.status == 200:
                    data = resp.read()
                    if data and len(data) > 200:
                        _THUMBNAIL_BYTE_CACHE[self.url] = data
                        if not self.is_cancelled:
                            self.loaded.emit(data)
        except Exception as e:
            logger.debug(f"Thumbnail download failed for {self.url}: {e}")

    @classmethod
    def load_async(
        cls,
        url: str,
        callback: Callable[[QPixmap], None],
        parent: Optional[QObject] = None,
    ) -> Optional[ThumbnailLoader]:
        """Convenience method to asynchronously fetch thumbnail and invoke callback with QPixmap."""
        if not url:
            return None

        if url in _THUMBNAIL_BYTE_CACHE:
            pix = QPixmap()
            if pix.loadFromData(_THUMBNAIL_BYTE_CACHE[url]):
                callback(pix)
                return None

        loader = cls(url, parent=parent)

        def _on_loaded(raw_bytes: bytes):
            pix = QPixmap()
            if pix.loadFromData(raw_bytes) and not pix.isNull():
                callback(pix)

        loader.loaded.connect(_on_loaded)
        loader.start()
        return loader
