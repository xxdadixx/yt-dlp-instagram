"""
gui/widgets/thumbnail_loader.py - Asynchronous Instagram thumbnail downloader.
Handles Instagram CDN headers, SSL certificate tolerance, and in-memory caching.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional, Callable

from PyQt6.QtCore import (
    QByteArray,
    QPoint,
    Qt,
    QObject,
    QRunnable,
    QThreadPool,
    pyqtSignal,
)
from PyQt6.QtGui import QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.image_viewer_dialog import ImageViewerDialog
from gui.widgets.thumbnail_loader import ThumbnailLoader

logger = logging.getLogger(__name__)

# In-memory cache for fetched thumbnail bytes
_THUMBNAIL_BYTE_CACHE: Dict[str, bytes] = {}

# Global shared pool capped at 4 concurrent downloads to prevent IP throttling
_THUMB_POOL = QThreadPool()
_THUMB_POOL.setMaxThreadCount(4)


class ThumbnailTask(QRunnable):
    """
    Background worker thread to fetch Instagram thumbnail bytes
    with headers matching Instagram's CDN expectations.
    """

    loaded = pyqtSignal(bytes)

    def __init__(self, url: str, signals: ThumbnailLoaderSignals):
        super().__init__()
        self.url = url
        self.signals = signals
        self.is_cancelled: bool = False

    def cancel(self) -> None:
        self.is_cancelled = True

    def run(self) -> None:
        if self.is_cancelled or not self.url or not self.url.startswith("http"):
            self.signals.failed.emit()
            return

        if self.url in _THUMBNAIL_BYTE_CACHE:
            self.signals.loaded.emit(_THUMBNAIL_BYTE_CACHE[self.url])
            return

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }
        req = urllib.request.Request(self.url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    raw_data = resp.read()
                    _THUMBNAIL_BYTE_CACHE[self.url] = raw_data
                    self.signals.loaded.emit(raw_data)
                    return
        except Exception as e:
            logger.debug(f"Thumbnail fetch failed: {e}")

        self.signals.failed.emit()

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

        loader = ThumbnailLoader(url, parent=parent)

        def _on_loaded(raw_bytes: bytes):
            pix = QPixmap()
            if pix.loadFromData(raw_bytes) and not pix.isNull():
                callback(pix)

        loader.loaded.connect(_on_loaded)
        loader.start()
        return loader


class ThumbnailLoader(QObject):
    loaded = pyqtSignal(bytes)
    failed = pyqtSignal()

    def __init__(self, url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.url = url
        self.signals = ThumbnailLoaderSignals()
        self.signals.loaded.connect(self.loaded.emit)
        self.signals.failed.connect(self.failed.emit)

    def start(self) -> None:
        task = ThumbnailTask(self.url, self.signals)
        _THUMB_POOL.start(task)

    def isRunning(self) -> bool:
        return False

    def wait(self, msecs: int = 200) -> None:
        pass


class ThumbnailLoaderSignals(QObject):
    loaded = pyqtSignal(bytes)
    failed = pyqtSignal()
