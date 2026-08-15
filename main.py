"""
main.py - Application Bootstrap and Lifecycle Management.
"""

import ctypes
import os
import sys
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from config.constants import APP_USER_MODEL_ID
from gui.main_window import MainWindow
from utils.file_utils import get_icon_path


def main() -> None:
    # กำหนด Windows Application User Model ID เพื่อให้แสดง Icon บน Taskbar ถูกต้อง
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass

    app = QApplication(sys.argv)

    icon_file = get_icon_path()
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()