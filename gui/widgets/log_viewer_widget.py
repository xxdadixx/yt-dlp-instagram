# File: gui/widgets/log_viewer_widget.py
from __future__ import annotations

import datetime
import logging
import os
from PyQt6.QtCore import QSize, Qt, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Header Bar: Title + Icon Action Buttons
        header_layout = QHBoxLayout()
        header_label = QLabel("Process Activity & Diagnostics")
        header_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #E0E0E0;"
        )
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        # Export Logs Button (Icon Only)
        self.export_btn = QPushButton(self)
        self.export_btn.setFixedSize(30, 28)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setToolTip("Export Activity Logs")
        export_icon = get_icon("export", color="#38BDF8", size=14)
        if export_icon:
            self.export_btn.setIcon(export_icon)
            self.export_btn.setIconSize(QSize(14, 14))

        self.export_btn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(56, 189, 248, 0.12);
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 6px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(56, 189, 248, 0.25);
                border: 1px solid #38BDF8;
            }
            QPushButton:pressed {
                background-color: rgba(14, 116, 144, 0.40);
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            """
        )
        self.export_btn.clicked.connect(self.export_logs)
        header_layout.addWidget(self.export_btn)

        # Clear Logs Button (Icon Only)
        self.clear_btn = QPushButton(self)
        self.clear_btn.setFixedSize(30, 28)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Clear Logs")
        clear_icon = get_icon("trash", color="#CBD5E1", size=13)
        if clear_icon:
            self.clear_btn.setIcon(clear_icon)
            self.clear_btn.setIconSize(QSize(13, 13))

        self.clear_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2D3238;
                border: 1px solid #3E444C;
                border-radius: 6px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #383E46;
            }
            QPushButton:pressed {
                background-color: #1F2327;
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            """
        )
        self.clear_btn.clicked.connect(self.clear_logs)
        header_layout.addWidget(self.clear_btn)

        layout.addLayout(header_layout)

        # 2. Log Output Viewport
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(2000)
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setStyleSheet(
            """
            QPlainTextEdit {
                background-color: #121417;
                color: #D4D4D4;
                border: 1px solid #282C34;
                border-radius: 6px;
                padding: 8px;
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
