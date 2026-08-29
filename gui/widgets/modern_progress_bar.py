from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QSizePolicy, QWidget


class ModernProgressBar(QFrame):
    """Smooth, rounded gradient progress bar without font size warnings."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._value: int = 0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setValue(self, val: int) -> None:
        self._value = max(0, min(100, int(val)))
        self.update()

    def value(self) -> int:
        return self._value

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        r = h / 2.0

        # Background track
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        painter.fillPath(bg_path, QColor(30, 32, 38))

        # Active progress bar fill
        if self._value > 0:
            fill_w = max(h, (w * self._value) / 100.0)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(QRectF(0, 0, fill_w, h), r, r)

            gradient = QLinearGradient(0, 0, w, 0)
            gradient.setColorAt(0.0, QColor("#F59E0B"))
            gradient.setColorAt(0.5, QColor("#EC4899"))
            gradient.setColorAt(1.0, QColor("#8B5CF6"))

            painter.fillPath(fill_path, gradient)

        painter.end()
