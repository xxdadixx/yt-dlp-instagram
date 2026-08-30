# File: gui/widgets/log_viewer_widget.py
from __future__ import annotations

import logging
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogViewerWidget(QWidget):
    """
    Real-time process activity viewer with auto-scroll and level highlighting.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header bar
        header_layout = QHBoxLayout()
        header_label = QLabel("Process Activity & Diagnostics")
        header_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #E0E0E0;"
        )
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        self.clear_btn = QPushButton("Clear Logs")
        self.clear_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #2D3238;
                color: #B0B0B0;
                border: 1px solid #3E444C;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #383E46;
                color: #FFFFFF;
            }
        """
        )
        self.clear_btn.clicked.connect(self.clear_logs)
        header_layout.addWidget(self.clear_btn)
        layout.addLayout(header_layout)

        # Log output text box
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(2000)  # Ring buffer to prevent memory bloat
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
            fmt.setForeground(QColor("#FF6B6B"))  # Coral Red
        elif level >= logging.WARNING:
            fmt.setForeground(QColor("#FFD93D"))  # Yellow
        elif level >= logging.INFO:
            fmt.setForeground(QColor("#6BCB77"))  # Mint Green
        else:
            fmt.setForeground(QColor("#8A99AD"))  # Slate Gray

        cursor.setCharFormat(fmt)
        cursor.insertText(message + "\n")
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    @pyqtSlot()
    def clear_logs(self) -> None:
        self.text_edit.clear()
