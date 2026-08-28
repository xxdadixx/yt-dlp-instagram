"""
gui/widgets/thumbnail_loader.py - Asynchronous thumbnail image fetcher.
Downloads image bytes in the background and emits raw bytes to avoid QPixmap thread-affinity issues.
"""

from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.request

try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError:

    class QThread:  # type: ignore
        def __init__(self, parent=None):
            pass

        def start(self):
            self.run()

    def pyqtSignal(*a):  # type: ignore
        class Signal:
            def __init__(self):
                self._slots = []

            def emit(self, *x):
                for s in self._slots:
                    try:
                        s(*x)
                    except Exception:
                        pass

            def connect(self, f):
                self._slots.append(f)

        return Signal()


logger = logging.getLogger(__name__)


class ThumbnailLoader(QThread):
    """
    Asynchronous background worker to fetch image thumbnail bytes over HTTPS.
    Emits raw image bytes to ensure safe QPixmap creation on the Qt GUI main thread.
    """

    loaded = pyqtSignal(bytes) if "pyqtSignal" in globals() else None

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = (url or "").strip()
        self._ssl_ctx = ssl._create_unverified_context()

    def run(self) -> None:
        if not self.url or not self.url.startswith("http"):
            return
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
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                data = resp.read()
                if data and self.loaded:
                    self.loaded.emit(data)
        except Exception as e:
            logger.debug(f"Thumbnail load failed for {self.url}: {e}")
