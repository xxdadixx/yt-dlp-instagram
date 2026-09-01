"""
gui/widgets/thumbnail_loader.py - High-performance asynchronous thumbnail loader
with thread-safe LRU memory eviction and bounded QThreadPool task dispatching.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class BoundedThumbnailCache:
    """Thread-safe LRU in-memory cache for raw thumbnail byte buffers with capacity enforcement."""

    def __init__(self, max_items: int = 256) -> None:
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._max_items = max_items

    def get(self, url: str) -> Optional[bytes]:
        with self._lock:
            if url not in self._cache:
                return None
            self._cache.move_to_end(url)
            return self._cache[url]

    def set(self, url: str, data: bytes) -> None:
        with self._lock:
            if url in self._cache:
                self._cache.move_to_end(url)
            self._cache[url] = data
            if len(self._cache) > self._max_items:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def contains(self, url: str) -> bool:
        with self._lock:
            return url in self._cache

    def size(self) -> int:
        with self._lock:
            return len(self._cache)


GLOBAL_THUMB_CACHE = BoundedThumbnailCache(max_items=300)


class ThumbnailTaskSignals(QObject):
    """Signals emitted across worker thread boundaries to UI consumers."""

    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)


class ThumbnailTask(QRunnable):
    """Worker task executing within a bounded QThreadPool to avoid thread exhaustion."""

    def __init__(self, url: str, signals: ThumbnailTaskSignals) -> None:
        super().__init__()
        self.url = url
        self.signals = signals
        self._is_cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._is_cancelled = True

    @pyqtSlot()
    def run(self) -> None:
        if not self.url or self._is_cancelled:
            return

        cached_data = GLOBAL_THUMB_CACHE.get(self.url)
        if cached_data is not None:
            if not self._is_cancelled:
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
            with urllib.request.urlopen(req, timeout=8) as resp:
                if self._is_cancelled:
                    return
                data = resp.read()
                if data and not self._is_cancelled:
                    GLOBAL_THUMB_CACHE.set(self.url, data)
                    self.signals.loaded.emit(data)
        except Exception as exc:
            if not self._is_cancelled:
                logger.debug("Failed to load thumbnail from %s: %s", self.url, exc)
                self.signals.error.emit(str(exc))


class ThumbnailLoader(QObject):
    """Adapter maintaining backwards-compatible API while dispatching tasks to QThreadPool."""

    loaded = pyqtSignal(bytes)

    def __init__(self, url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.url = url
        self._signals = ThumbnailTaskSignals()
        self._signals.loaded.connect(self.loaded.emit)
        self._task: Optional[ThumbnailTask] = None

    def start(self) -> None:
        if not self.url:
            return
        cached = GLOBAL_THUMB_CACHE.get(self.url)
        if cached is not None:
            self.loaded.emit(cached)
            return
        self._task = ThumbnailTask(self.url, self._signals)
        QThreadPool.globalInstance().start(self._task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    @staticmethod
    def load_cached_pixmap(url: str, callback: Callable[[QPixmap], None]) -> bool:
        cached_data = GLOBAL_THUMB_CACHE.get(url)
        if cached_data:
            pix = QPixmap()
            if pix.loadFromData(cached_data):
                callback(pix)
                return True
        return False
