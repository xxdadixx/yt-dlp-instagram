"""
gui/widgets/thumbnail_loader.py - Asynchronous thumbnail downloader with thread-safe byte caching.
"""

from __future__ import annotations

import logging
import threading
import urllib.request
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, QRunnable, QThread, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Thread-safe in-memory cache for raw thumbnail byte buffers."""

    _lock = threading.RLock()
    _cache: Dict[str, bytes] = {}

    @classmethod
    def get(cls, url: str) -> Optional[bytes]:
        with cls._lock:
            return cls._cache.get(url)

    @classmethod
    def set(cls, url: str, data: bytes) -> None:
        with cls._lock:
            cls._cache[url] = data

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    @classmethod
    def contains(cls, url: str) -> bool:
        with cls._lock:
            return url in cls._cache

    @classmethod
    def size(cls) -> int:
        with cls._lock:
            return len(cls._cache)


_THUMBNAIL_POOL = QThreadPool.globalInstance()
_THUMBNAIL_POOL.setMaxThreadCount(4)


class ThumbnailTaskSignals(QObject):
    """Signals for background runnable tasks."""

    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)


class ThumbnailTask(QRunnable):
    """Background worker task executing within QThreadPool for thumbnail resolution."""

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = ThumbnailTaskSignals()
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self) -> None:
        if not self.url:
            return

        cached_data = ThumbnailCache.get(self.url)
        if cached_data is not None:
            self.signals.loaded.emit(cached_data)
            return

        try:
            req = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                if data:
                    ThumbnailCache.set(self.url, data)
                    self.signals.loaded.emit(data)
        except Exception as exc:
            logger.debug("Failed to fetch thumbnail %s: %s", self.url, exc)
            self.signals.error.emit(str(exc))


class ThumbnailLoader(QThread):
    """Thread-safe sequential loader maintaining compatibility with MediaCard widgets."""

    loaded = pyqtSignal(bytes)

    def __init__(self, url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.url = url
        self._is_cancelled = False

    def run(self) -> None:
        if not self.url or self._is_cancelled:
            return

        cached_data = ThumbnailCache.get(self.url)
        if cached_data is not None:
            self.loaded.emit(cached_data)
            return

        try:
            req = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if self._is_cancelled:
                    return
                data = resp.read()
                if data and not self._is_cancelled:
                    ThumbnailCache.set(self.url, data)
                    self.loaded.emit(data)
        except Exception as exc:
            logger.debug("Failed to load thumbnail %s: %s", self.url, exc)

    def cancel(self) -> None:
        self._is_cancelled = True

    @staticmethod
    def load_cached_pixmap(url: str, callback: Callable[[QPixmap], None]) -> bool:
        cached_data = ThumbnailCache.get(url)
        if cached_data:
            pix = QPixmap()
            if pix.loadFromData(cached_data):
                callback(pix)
                return True
        return False
