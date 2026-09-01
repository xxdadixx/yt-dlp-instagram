from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import Qt, QRectF, QPropertyAnimation, pyqtProperty, QEasingCurve
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QSizePolicy, QWidget


class ModernProgressBar(QFrame):
    """
    Liquid Glass Shimmer Progress Bar (Google M3 Principles).
    Features smooth lerp-interpolated progress tweening via QPropertyAnimation,
    specular micro-bevel edge strokes, and active shimmer beam dynamics.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._target_value: float = 0.0
        self._animated_value: float = 0.0
        self._shimmer_phase: float = 0.0

        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Smooth progress interpolation tween (250ms OutCubic)
        self._value_anim = QPropertyAnimation(self, b"animatedValue", self)
        self._value_anim.setDuration(250)
        self._value_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Active shimmer sweep
        self._shimmer_anim = QPropertyAnimation(self, b"shimmerPhase", self)
        self._shimmer_anim.setDuration(1200)
        self._shimmer_anim.setStartValue(0.0)
        self._shimmer_anim.setEndValue(1.0)
        self._shimmer_anim.setLoopCount(-1)

    @pyqtProperty(float)
    def animatedValue(self) -> float:
        return self._animated_value

    @animatedValue.setter
    def animatedValue(self, val: float) -> None:
        self._animated_value = val
        self.update()

    @pyqtProperty(float)
    def shimmerPhase(self) -> float:
        return self._shimmer_phase

    @shimmerPhase.setter
    def shimmerPhase(self, val: float) -> None:
        self._shimmer_phase = val
        self.update()

    def setValue(self, val: int) -> None:
        clamped = max(0.0, min(100.0, float(val)))
        if clamped == self._target_value:
            return

        self._target_value = clamped
        self._value_anim.stop()
        self._value_anim.setStartValue(self._animated_value)
        self._value_anim.setEndValue(self._target_value)
        self._value_anim.start()

        # Run shimmer animation when progress is actively moving
        if 0.0 < clamped < 100.0:
            if self._shimmer_anim.state() != QPropertyAnimation.State.Running:
                self._shimmer_anim.start()
        else:
            if self._shimmer_anim.state() == QPropertyAnimation.State.Running:
                self._shimmer_anim.stop()
                self._shimmer_phase = 0.0

    def value(self) -> int:
        return int(round(self._target_value))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        r = h / 2.0
        rect = QRectF(0.0, 0.0, w, h)

        # 1. Base Frosted Trough Layer
        trough_path = QPainterPath()
        trough_path.addRoundedRect(rect, r, r)
        trough_grad = QLinearGradient(0, 0, 0, h)
        trough_grad.setColorAt(0.0, QColor(20, 18, 28, 220))
        trough_grad.setColorAt(1.0, QColor(14, 12, 20, 240))
        painter.fillPath(trough_path, trough_grad)

        # Trough Micro-Bevel Stroke
        trough_pen = QPen(QColor(255, 255, 255, 18), 0.75)
        painter.setPen(trough_pen)
        painter.drawPath(trough_path)

        # 2. Active Liquid Gradient Fill
        if self._animated_value > 0.0:
            fill_w = max(h, (w * self._animated_value) / 100.0)
            fill_rect = QRectF(0.0, 0.0, fill_w, h)
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, r, r)

            # Sunset Brand Progression: #833AB4 -> #E1306C -> #FD1D1D -> #FCAF45
            bar_grad = QLinearGradient(0, 0, fill_w, 0)
            bar_grad.setColorAt(0.0, QColor(131, 58, 180))
            bar_grad.setColorAt(0.4, QColor(225, 48, 108))
            bar_grad.setColorAt(0.8, QColor(253, 29, 29))
            bar_grad.setColorAt(1.0, QColor(252, 175, 69))
            painter.fillPath(fill_path, bar_grad)

            # 3. Dynamic Specular Shimmer Beam
            if self._shimmer_anim.state() == QPropertyAnimation.State.Running:
                shimmer_center = fill_w * self._shimmer_phase
                shimmer_beam = QLinearGradient(
                    shimmer_center - 35.0, 0.0, shimmer_center + 35.0, 0.0
                )
                shimmer_beam.setColorAt(0.0, QColor(255, 255, 255, 0))
                shimmer_beam.setColorAt(0.5, QColor(255, 255, 255, 110))
                shimmer_beam.setColorAt(1.0, QColor(255, 255, 255, 0))

                painter.save()
                painter.setClipPath(fill_path)
                painter.fillPath(fill_path, shimmer_beam)
                painter.restore()

            # Specular Top Highlight Stroke
            top_pen = QPen(QColor(255, 255, 255, 75), 0.6)
            painter.setPen(top_pen)
            painter.drawPath(fill_path)

        painter.end()
