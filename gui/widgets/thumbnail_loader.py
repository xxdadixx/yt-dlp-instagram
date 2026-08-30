"""
gui/widgets/thumbnail_loader.py - Asynchronous thumbnail downloader with QThreadPool and byte caching.
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, QRunnable, QThread, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)

# Global in-memory raw byte cache (URL -> bytes)
_THUMBNAIL_BYTE_CACHE: Dict[str, bytes] = {}

# Bounded global thread pool for thumbnail downloads (max 4 workers)
_THUMBNAIL_POOL = QThreadPool.globalInstance()
_THUMBNAIL_POOL.setMaxThreadCount(4)


class ThumbnailTaskSignals(QObject):
    """Signals for background runnable tasks."""

    loaded = pyqtSignal(bytes)
    error = pyqtSignal(str)


class ThumbnailTask(QRunnable):
    """
    Background worker task running on QThreadPool.
    Downloads raw image bytes without creating QPixmap on background threads.
    """

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = ThumbnailTaskSignals()
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self) -> None:
        if not self.url:
            return

        if self.url in _THUMBNAIL_BYTE_CACHE:
            self.signals.loaded.emit(_THUMBNAIL_BYTE_CACHE[self.url])
            return

        try:
            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                if data:
                    _THUMBNAIL_BYTE_CACHE[self.url] = data
                    self.signals.loaded.emit(data)
        except Exception as e:
            logger.debug("Failed to fetch thumbnail %s: %s", self.url, e)
            self.signals.error.emit(str(e))


class ThumbnailLoader(QThread):
    """
    QThread-based thumbnail loader maintaining backwards compatibility with MediaCard.
    Emits raw bytes to ensure Qt thread safety.
    """

    loaded = pyqtSignal(bytes)

    def __init__(self, url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.url = url
        self._is_cancelled = False

    def run(self) -> None:
        if not self.url or self._is_cancelled:
            return

        if self.url in _THUMBNAIL_BYTE_CACHE:
            self.loaded.emit(_THUMBNAIL_BYTE_CACHE[self.url])
            return

        try:
            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if self._is_cancelled:
                    return
                data = resp.read()
                if data:
                    _THUMBNAIL_BYTE_CACHE[self.url] = data
                    self.loaded.emit(data)
        except Exception as e:
            logger.debug("Failed to load thumbnail %s: %s", self.url, e)

    def cancel(self) -> None:
        self._is_cancelled = True

    @staticmethod
    def load_cached_pixmap(url: str, callback: Callable[[QPixmap], None]) -> bool:
        """
        Helper for main GUI thread to quickly resolve cached pixmaps without worker dispatch.
        """
        if url in _THUMBNAIL_BYTE_CACHE:
            pix = QPixmap()
            if pix.loadFromData(_THUMBNAIL_BYTE_CACHE[url]):
                callback(pix)
                return True
        return False
