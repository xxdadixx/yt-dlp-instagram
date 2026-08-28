"""
gui/widgets/thumbnail_loader.py - Asynchronous thumbnail image fetcher with caching and safe cancellation.
Downloads image bytes in the background and emits raw bytes to avoid QPixmap thread-affinity issues.
"""

from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.request
from typing import Dict, Optional

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError:

    class QThread:  # type: ignore
        def __init__(self, parent=None):
            pass

        def isRunning(self) -> bool:
            return False

        def start(self):
            self.run()

        def cancel(self):
            pass

        def wait(self, timeout=None):
            pass

    def pyqtSignal(*a):  # type: ignore
        class Signal:
            def __init__(self):
                self._slots = []

            def emit(self, *x):
                for s in list(self._slots):
                    try:
                        s(*x)
                    except Exception:
                        pass

            def connect(self, f):
                self._slots.append(f)

            def disconnect(self, f=None):
                if f is None:
                    self._slots.clear()
                elif f in self._slots:
                    self._slots.remove(f)

        return Signal()


logger = logging.getLogger(__name__)


class ThumbnailCache:
    """In-memory cache for downloaded thumbnail byte data to eliminate redundant network requests."""

    _cache: Dict[str, bytes] = {}
    _max_size: int = 500

    @classmethod
    def get(cls, url: str) -> Optional[bytes]:
        return cls._cache.get(url)

    @classmethod
    def set(cls, url: str, data: bytes) -> None:
        if len(cls._cache) >= cls._max_size:
            cls._cache.clear()
        cls._cache[url] = data

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()


class ThumbnailLoader(QThread):
    """
    Asynchronous background worker to fetch image thumbnail bytes over HTTPS.
    Emits raw image bytes to ensure safe QPixmap creation on the Qt GUI main thread.
    """

    loaded = pyqtSignal(bytes) if "pyqtSignal" in globals() else None

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url: str = (url or "").strip()
        self._is_cancelled: bool = False
        self._ssl_ctx = ssl._create_unverified_context()

    def cancel(self) -> None:
        """Flags the thumbnail loader to cancel and suppress signal emissions."""
        self._is_cancelled = True

    def run(self) -> None:
        if not self.url or not self.url.startswith("http") or self._is_cancelled:
            return

        # 1. Check memory cache first
        cached_data = ThumbnailCache.get(self.url)
        if cached_data:
            if not self._is_cancelled and self.loaded:
                self.loaded.emit(cached_data)
            return

        # 2. Download from network
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }
            req = urllib.request.Request(self.url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=8) as resp:
                if self._is_cancelled:
                    return
                data = resp.read()
                if data and not self._is_cancelled:
                    ThumbnailCache.set(self.url, data)
                    if self.loaded:
                        self.loaded.emit(data)
        except Exception as e:
            logger.debug(f"Thumbnail load failed for {self.url}: {e}")
