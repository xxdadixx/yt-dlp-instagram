"""
gui/widgets/thumbnail_loader.py - Async thumbnail downloader QThread.
"""

import urllib.request
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from config.constants import DESKTOP_UA


class ThumbnailLoader(QThread):
    loaded = pyqtSignal(QImage)  # ส่ง QImage แทน QPixmap เพื่อความปลอดภัยของ Thread

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._is_running = True

    def cancel(self) -> None:
        self._is_running = False

    def run(self) -> None:
        if not self._is_running or not self.url:
            return
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": DESKTOP_UA})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if not self._is_running:
                    return
                raw_data = resp.read()
                image = QImage.fromData(raw_data)
                if not image.isNull() and self._is_running:
                    self.loaded.emit(image)
        except Exception:
            pass
