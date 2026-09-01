# File: gui/widgets/log_viewer_widget.py
from __future__ import annotations

import datetime
import logging
import os
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.icons import get_icon

logger = logging.getLogger(__name__)


class LogViewerWidget(QWidget):
    """
    Real-time process activity viewer with icon-only actions,
    ring-buffered memory protection, and timestamped disk export.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # 1. Header Bar: Glass Badge + Title + Action Buttons
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        self.diagnostic_badge = GlassDiagnosticBadge(self)
        header_layout.addWidget(self.diagnostic_badge)

        header_label = QLabel("Process Activity & Diagnostics")
        header_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #E2E8F0;"
        )
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        # Export Logs Button
        self.export_btn = QPushButton(self)
        self.export_btn.setObjectName("GlassActionButton")
        self.export_btn.setFixedSize(36, 32)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setToolTip("Export Activity Logs")
        export_icon = get_icon("export", color="#38BDF8", size=15)
        if export_icon:
            self.export_btn.setIcon(export_icon)
            self.export_btn.setIconSize(QSize(15, 15))
        self.export_btn.clicked.connect(self.export_logs)
        header_layout.addWidget(self.export_btn)

        # Clear Logs Button
        self.clear_btn = QPushButton(self)
        self.clear_btn.setObjectName("GlassActionButton")
        self.clear_btn.setFixedSize(36, 32)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Clear Logs")
        clear_icon = get_icon("trash", color="#CBD5E1", size=14)
        if clear_icon:
            self.clear_btn.setIcon(clear_icon)
            self.clear_btn.setIconSize(QSize(14, 14))
        self.clear_btn.clicked.connect(self.clear_logs)
        header_layout.addWidget(self.clear_btn)

        layout.addLayout(header_layout)

        # 2. Log Output Viewport (Frosted Dark Glass)
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(2000)
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: rgba(18, 16, 26, 0.75);
                color: #D4D4D4;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                padding: 10px;
            }
            """
        )
        layout.addWidget(self.text_edit)

    @pyqtSlot(str, int)
    def append_log(self, message: str, level: int) -> None:
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        if level >= logging.ERROR:
            fmt.setForeground(QColor("#FF6B6B"))
        elif level >= logging.WARNING:
            fmt.setForeground(QColor("#FFD93D"))
        elif level >= logging.INFO:
            fmt.setForeground(QColor("#6BCB77"))
        else:
            fmt.setForeground(QColor("#8A99AD"))

        cursor.setCharFormat(fmt)
        cursor.insertText(message + "\n")
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    @pyqtSlot()
    def clear_logs(self) -> None:
        self.text_edit.clear()

    @pyqtSlot()
    def export_logs(self) -> None:
        log_content = self.text_edit.toPlainText().strip()
        if not log_content:
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"instagram_downloader_log_{timestamp}.log"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Activity Logs",
            default_filename,
            "Log Files (*.log);;Text Files (*.txt);;All Files (*.*)",
        )

        if not file_path:
            return

        try:
            target_dir = os.path.dirname(os.path.abspath(file_path))
            os.makedirs(target_dir, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(log_content + "\n")

            logger.info("Diagnostics log successfully exported to: %s", file_path)
        except Exception as exc:
            logger.error("Failed to export log file '%s': %s", file_path, exc)

    def get_logs(self) -> str:
        return self.text_edit.toPlainText()


class GlassDiagnosticBadge(QFrame):
    """
    Sub-surface glass pod providing real-time engine telemetry and pulse indicator.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self._status_color = QColor(16, 185, 129)  # Green / Ready

    def set_status_color(self, color_hex: str) -> None:
        self._status_color = QColor(color_hex)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)

        # 1. Frosted Plate
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(32, 28, 48, 180))
        grad.setColorAt(1.0, QColor(16, 14, 25, 210))
        painter.fillPath(path, grad)

        # 2. Specular Edge
        pen_grad = QLinearGradient(0, 0, w, h)
        pen_grad.setColorAt(0.0, QColor(255, 255, 255, 60))
        pen_grad.setColorAt(1.0, QColor(255, 255, 255, 10))
        painter.setPen(QPen(pen_grad, 1.0))
        painter.drawPath(path)

        # 3. Pulsing Status Node
        node_center = QPointF(w / 2.0, h / 2.0)
        glow = QRadialGradient(node_center, 6.0)
        glow.setColorAt(0.0, self._status_color)
        glow.setColorAt(
            1.0,
            QColor(
                self._status_color.red(),
                self._status_color.green(),
                self._status_color.blue(),
                0,
            ),
        )
        painter.fillPath(path, glow)

        painter.setBrush(self._status_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(node_center, 3.0, 3.0)

        painter.end()
