"""
main.py - Application Bootstrap, Per-Monitor DPI Scaling, and Lifecycle Management.
"""

import ctypes
import os
import sys
import urllib.parse
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from config.constants import APP_USER_MODEL_ID
from gui.main_window import MainWindow
from utils.file_utils import get_icon_path


def main() -> None:
    # Enable Windows Per-Monitor High-DPI Awareness and App User Model ID
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID
            )
        except Exception:
            pass

    app = QApplication(sys.argv)

    icon_file = get_icon_path()
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    window = MainWindow()

    # ตรวจสอบและดักจับลิงก์ที่ถูกส่งมาจากเบราว์เซอร์ผ่าน Protocol Scheme
    if len(sys.argv) > 1:
        raw_arg = sys.argv[1].strip()
        if raw_arg.startswith("igdownloader://"):
            clean_url = urllib.parse.unquote(raw_arg.replace("igdownloader://", "", 1))
        else:
            clean_url = raw_arg

        if clean_url and hasattr(window, "url_container"):
            from core.parser import normalize_url
            norm = normalize_url(clean_url) or clean_url
            window.url_container.add_url_chip(norm)
            window.tab_widget.setCurrentIndex(1)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()