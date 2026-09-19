"""
gui/widgets/status_overlay.py - Minimalist Full-Viewport Radial Spoke Loading Veil.
Renders an authentic 16-spoke fading indicator with vector QPainter paths,
letter-spaced typography, and non-blocking worker telemetry.
"""

from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.icons import get_icon


class RadialSpokeSpinner(QWidget):
    """Vector-rendered 16-spoke circular activity indicator."""

    def __init__(
        self,
        num_spokes: int = 16,
        inner_radius: float = 24.0,
        spoke_length: float = 16.0,
        spoke_width: float = 5.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.num_spokes = num_spokes
        self.inner_radius = inner_radius
        self.spoke_length = spoke_length
        self.spoke_width = spoke_width
        self._current_step: int = 0

        dim = int((inner_radius + spoke_length + 8.0) * 2)
        self.setFixedSize(dim, dim)

        self._timer = QTimer(self)
        self._timer.setInterval(65)
        self._timer.timeout.connect(self._rotate_step)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def _rotate_step(self) -> None:
        self._current_step = (self._current_step + 1) % self.num_spokes
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        angle_step = 360.0 / self.num_spokes

        for i in range(self.num_spokes):
            # Calculate tail fade: 0 is head (brightest), (num_spokes - 1) is tail (darkest)
            distance = (self._current_step - i) % self.num_spokes
            ratio = 1.0 - (distance / float(self.num_spokes))
            alpha = int(28 + (227 * (ratio**2.2)))

            painter.save()
            painter.translate(cx, cy)
            painter.rotate(i * angle_step)

            spoke_rect = QRectF(
                -self.spoke_width / 2.0,
                -(self.inner_radius + self.spoke_length),
                self.spoke_width,
                self.spoke_length,
            )
            path = QPainterPath()
            path.addRoundedRect(
                spoke_rect, self.spoke_width / 2.2, self.spoke_width / 2.2
            )

            painter.fillPath(path, QColor(255, 255, 255, alpha))
            painter.restore()

        painter.end()


class StatusOverlay(QFrame):
    cancelled: pyqtSignal = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._is_visible_target: bool = False
        self._fade_anim: Optional[QPropertyAnimation] = None

        self._init_ui()
        self.hide()

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(14)
        center_col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 1. 16-Spoke Radial Spinner
        self.spinner = RadialSpokeSpinner(
            num_spokes=16,
            inner_radius=22.0,
            spoke_length=15.0,
            spoke_width=4.5,
            parent=self,
        )
        center_col.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        # 2. Uppercase Letter-Spaced Status Label
        self.lbl_title = QLabel("LOADING..", self)
        font = QFont("Segoe UI Variable Display", 11, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.8)
        self.lbl_title.setFont(font)
        self.lbl_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self.lbl_title, alignment=Qt.AlignmentFlag.AlignCenter)

        # 3. Muted Telemetry / Progress Description
        self.lbl_status = QLabel("", self)
        self.lbl_status.setFont(QFont("Segoe UI Variable Display", 9))
        self.lbl_status.setStyleSheet("color: #71717A; background: transparent;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_col.addWidget(self.lbl_status, alignment=Qt.AlignmentFlag.AlignCenter)

        # 4. Minimalist Text-Only Cancel Action
        self.btn_cancel = QPushButton("CANCEL", self)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setFont(
            QFont("Segoe UI Variable Display", 8, QFont.Weight.Bold)
        )
        self.btn_cancel.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                color: #52525B;
                border: none;
                letter-spacing: 1.5px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                color: #EF4444;
            }
            QPushButton:pressed {
                color: #B91C1C;
            }
            """
        )
        self.btn_cancel.clicked.connect(self.cancelled.emit)
        center_col.addWidget(self.btn_cancel, alignment=Qt.AlignmentFlag.AlignCenter)

        root_layout.addLayout(center_col)

    def paintEvent(self, event) -> None:
        """Draws an edge-to-edge semi-transparent black scrim across the entire viewport."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 225))
        painter.end()

    def show_overlay(self, mode: str = "inspect", title: str = "") -> None:
        self._is_visible_target = True
        raw_title = (
            title.upper()
            if title
            else ("INSPECTING..." if mode == "inspect" else "DOWNLOADING...")
        )
        self.lbl_title.setText(raw_title)
        self.lbl_status.setText("")

        self.reposition_in_parent()
        self.spinner.start()
        self.show()
        self.raise_()

        if (
            self._fade_anim
            and self._fade_anim.state() == QPropertyAnimation.State.Running
        ):
            self._fade_anim.stop()

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(160)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()

    def hide_overlay(self) -> None:
        if not self._is_visible_target:
            return
        self._is_visible_target = False

        if (
            self._fade_anim
            and self._fade_anim.state() == QPropertyAnimation.State.Running
        ):
            self._fade_anim.stop()

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(140)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        def _on_hidden():
            if not self._is_visible_target:
                self.spinner.stop()
                self.hide()

        self._fade_anim.finished.connect(_on_hidden)
        self._fade_anim.start()

    def update_progress(self, percent: int) -> None:
        clamped = max(0, min(100, percent))
        current_title = self.lbl_title.text().split(" [")[0]
        self.lbl_title.setText(f"{current_title} [{clamped}%]")

    def update_status(self, message: str) -> None:
        clean = message.replace("...", "").strip()
        self.lbl_status.setText(clean)

    def update_target(self, target_text: str) -> None:
        pass

    def reposition_in_parent(self) -> None:
        """Expands the scrim to cover the full viewport dimensions."""
        parent = self.parentWidget()
        if parent:
            self.setGeometry(0, 0, parent.width(), parent.height())
