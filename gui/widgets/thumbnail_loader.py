"""
gui/widgets/thumbnail_loader.py - Asynchronous thumbnail image fetcher with caching, thread-safe pooling, and safe cancellation.
Downloads image bytes in the background via QThreadPool to prevent OS thread leaks and emits raw bytes to avoid QPixmap thread-affinity issues.
"""

from __future__ import annotations

import logging
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Set

try:
    from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
except ImportError:

    class QObject:  # type: ignore

        def __init__(self, parent=None):
            pass

    class QRunnable:  # type: ignore

        def __init__(self):
            pass

    class QThreadPool:  # type: ignore

        _instance = None

        @classmethod
        def globalInstance(cls):
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

        def setMaxThreadCount(self, count: int):
            pass

        def start(self, runnable):
            if hasattr(runnable, "run"):
                runnable.run()

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
                if f not in self._slots:
                    self._slots.append(f)

            def disconnect(self, f=None):
                if f is None:
                    self._slots.clear()
                elif f in self._slots:
                    self._slots.remove(f)

        return Signal()


logger = logging.getLogger(__name__)


class ThumbnailCache:
    """Thread-safe in-memory cache for downloaded thumbnail byte data."""

    _lock: threading.Lock = threading.Lock()
    _cache: Dict[str, bytes] = {}
    _max_size: int = 500

    @classmethod
    def get(cls, url: str) -> Optional[bytes]:
        with cls._lock:
            return cls._cache.get(url)

    @classmethod
    def set(cls, url: str, data: bytes) -> None:
        with cls._lock:
            if len(cls._cache) >= cls._max_size:
                evict_count = max(1, cls._max_size // 4)
                keys_to_evict = list(cls._cache.keys())[:evict_count]
                for k in keys_to_evict:
                    cls._cache.pop(k, None)
            cls._cache[url] = data

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    @classmethod
    def has(cls, url: str) -> bool:
        with cls._lock:
            return url in cls._cache


class _ThumbnailSignalBridge(QObject):
    loaded = pyqtSignal(bytes) if "pyqtSignal" in globals() else None


class ThumbnailTask(QRunnable):
    """Runnable task executed within QThreadPool for non-blocking thumbnail fetching."""

    def __init__(self, url: str, loader_ref: ThumbnailLoader):
        super().__init__()
        self.url = url
        self.loader_ref = loader_ref
        self._ssl_ctx = ssl._create_unverified_context()

    def run(self) -> None:
        if not self.url or not self.url.startswith("http"):
            return

        loader = self.loader_ref
        if not loader or loader.is_cancelled:
            return

        # 1. Check memory cache
        cached_data = ThumbnailCache.get(self.url)
        if cached_data:
            if (
                loader
                and not loader.is_cancelled
                and loader.signals
                and loader.signals.loaded
            ):
                loader.signals.loaded.emit(cached_data)
            return

        # 2. Network fetch
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
                "Sec-Fetch-Dest": "image",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }
            req = urllib.request.Request(self.url, headers=headers)
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=8) as resp:
                if not loader or loader.is_cancelled:
                    return
                data = resp.read()
                if data and loader and not loader.is_cancelled:
                    ThumbnailCache.set(self.url, data)
                    if (
                        loader.signals
                        and loader.signals.loaded
                        and not loader.is_cancelled
                    ):
                        loader.signals.loaded.emit(data)
        except Exception as e:
            logger.debug(f"Thumbnail load failed for {self.url}: {e}")


class ThumbnailLoader:
    """High-performance thumbnail loader facade backed by QThreadPool."""

    def __init__(self, url: str, parent=None):
        self.url: str = (url or "").strip()
        self.is_cancelled: bool = False
        self.signals = _ThumbnailSignalBridge(parent)
        self.loaded = self.signals.loaded

    def cancel(self) -> None:
        self.is_cancelled = True
        if self.signals and hasattr(self.signals.loaded, "disconnect"):
            try:
                self.signals.loaded.disconnect()
            except Exception:
                pass

    def start(self) -> None:
        if not self.url or not self.url.startswith("http") or self.is_cancelled:
            return

        cached_data = ThumbnailCache.get(self.url)
        if cached_data:
            if not self.is_cancelled and self.loaded:
                self.loaded.emit(cached_data)
            return

        task = ThumbnailTask(self.url, self)
        pool = QThreadPool.globalInstance()
        pool.start(task)

    def run(self) -> None:
        task = ThumbnailTask(self.url, self)
        task.run()

    def wait(self, timeout: Optional[int] = None) -> bool:
        return True

    def isRunning(self) -> bool:
        return not self.is_cancelled
