"""
gui/widgets/no_scroll_combo.py - QComboBox subclass ignoring mouse wheel events to protect card settings.
"""

from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QComboBox


class NoScrollComboBox(QComboBox):
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()
