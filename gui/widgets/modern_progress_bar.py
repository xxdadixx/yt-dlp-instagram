"""
gui/widgets/modern_progress_bar.py - High-Performance Animated Progress Bar with Shimmer Effect & Smooth Lerp.
"""

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class ModernProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)

        self._current_value = 0.0
        self._target_value = 0.0
        self._shimmer_offset = 0.0

        # Animation Loop (60 FPS ~16ms)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_animation_frame)
        self._anim_timer.start(16)

    def setValue(self, value: float) -> None:
        self._target_value = max(0.0, min(100.0, float(value)))
        if not self._anim_timer.isActive():
            self._anim_timer.start(16)  # เริ่ม Timer เฉพาะเมื่อมีการเปลี่ยนแปลงค่า

    def value(self) -> float:
        return self._target_value

    def reset(self) -> None:
        self._current_value = 0.0
        self._target_value = 0.0
        self._shimmer_offset = 0.0
        self.update()

    def _on_animation_frame(self) -> None:
        needs_repaint = False
        diff = self._target_value - self._current_value

        if abs(diff) > 0.05:
            self._current_value += diff * 0.15
            needs_repaint = True
        elif self._current_value != self._target_value:
            self._current_value = self._target_value
            needs_repaint = True

        if 0.0 < self._current_value < 100.0:
            self._shimmer_offset = (self._shimmer_offset + 0.018) % 1.0
            needs_repaint = True
        elif self._current_value in (0.0, 100.0) and abs(diff) <= 0.05:
            # หยุด Timer เมื่อแอนิเมชันเสร็จสมบูรณ์และไม่มีการเคลื่อนไหว เพื่อประหยัด CPU 0%
            self._anim_timer.stop()

        if needs_repaint:
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        radius = h / 2.0

        # --- Draw Background Track ---
        track_gradient = QLinearGradient(0, 0, 0, h)
        track_gradient.setColorAt(0.0, QColor(22, 22, 28))
        track_gradient.setColorAt(1.0, QColor(32, 32, 42))

        painter.setPen(QPen(QColor(56, 56, 74, 150), 1.0))
        painter.setBrush(QBrush(track_gradient))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)

        # --- Draw Fill Progress ---
        if self._current_value > 0.0:
            fill_width = max(h, (self._current_value / 100.0) * w)

            # Core Instagram Gradient
            bar_gradient = QLinearGradient(0, 0, w, 0)
            bar_gradient.setColorAt(0.0, QColor("#fa7e1e"))
            bar_gradient.setColorAt(0.5, QColor("#d62976"))
            bar_gradient.setColorAt(1.0, QColor("#9b51e0"))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bar_gradient))
            painter.drawRoundedRect(
                QRectF(0.5, 0.5, fill_width - 1.0, h - 1.0), radius, radius
            )

            # --- Draw Sweeping Shimmer Reflection ---
            if 0.0 < self._current_value < 100.0:
                shimmer_x = fill_width * self._shimmer_offset
                shimmer_grad = QLinearGradient(shimmer_x - 40, 0, shimmer_x + 40, 0)
                shimmer_grad.setColorAt(0.0, QColor(255, 255, 255, 0))
                shimmer_grad.setColorAt(0.5, QColor(255, 255, 255, 110))
                shimmer_grad.setColorAt(1.0, QColor(255, 255, 255, 0))

                painter.setBrush(QBrush(shimmer_grad))
                painter.drawRoundedRect(
                    QRectF(0.5, 0.5, fill_width - 1.0, h - 1.0), radius, radius
                )

        # --- Draw Percentage Text ---
        if self._current_value > 0.0:
            painter.setPen(QColor(255, 255, 255, 220))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            pct_str = f"{int(self._current_value)}%"
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, pct_str)

        painter.end()
