"""
gui/widgets/media_card.py - Interactive Media Card Widget with Selection & Quality Dropdown.
"""

import os
import subprocess
import sys
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from config.translations import TRANSLATIONS
from gui.styles import CARD_DEFAULT_QSS, CARD_SELECTED_QSS
from gui.widgets.no_scroll_combo import NoScrollComboBox
from gui.widgets.thumbnail_loader import ThumbnailLoader


class MediaCardWidget(QFrame):
    clicked = pyqtSignal(object, object)
    removed = pyqtSignal(object)

    def __init__(self, data: dict, lang: str = "th"):
        super().__init__()
        self.data = data
        self.lang = lang
        self.is_selected = False
        self.is_completed = False
        self.saved_file_path = None
        self.thumb_loader = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.init_ui()
        self.update_style()
        self.load_thumbnail_async()

    def init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.lbl_thumb = QLabel()
        self.lbl_thumb.setFixedSize(70, 70)
        self.lbl_thumb.setStyleSheet("background-color: #141419; border-radius: 5px;")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setText("Loading...")
        layout.addWidget(self.lbl_thumb)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        top_row = QHBoxLayout()
        self.lbl_uploader = QLabel(f"@{self.data.get('uploader', 'Instagram')}")
        self.lbl_uploader.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        top_row.addWidget(self.lbl_uploader)

        m_type = self.data.get("media_type")
        if m_type in ("story", "highlight"):
            badge_text = TRANSLATIONS[self.lang]["badge_story"]
            badge_color = "#9b51e0"
        elif m_type == "carousel":
            badge_text = TRANSLATIONS[self.lang]["badge_carousel"].format(
                count=self.data.get("slides_count", 1)
            )
            badge_color = "#fa7e1e"
        elif m_type == "video":
            badge_text = TRANSLATIONS[self.lang]["badge_video"]
            badge_color = "#d62976"
        else:
            badge_text = TRANSLATIONS[self.lang]["badge_photo"]
            badge_color = "#4a90e2"

        self.lbl_badge = QLabel(f" {badge_text} ")
        self.lbl_badge.setStyleSheet(
            f"background-color: {badge_color}; color: white; border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px 6px;"
        )
        top_row.addWidget(self.lbl_badge)
        top_row.addStretch()
        info_layout.addLayout(top_row)

        self.lbl_sub = QLabel(f"ID: {self.data.get('shortcode')} | {self.data.get('url')[:45]}...")
        self.lbl_sub.setStyleSheet("color: #888888; font-size: 10px;")
        info_layout.addWidget(self.lbl_sub)

        bottom_row = QHBoxLayout()
        lbl_q = QLabel(TRANSLATIONS[self.lang]["lbl_quality"])
        lbl_q.setStyleSheet("font-size: 11px; color: #cccccc;")
        bottom_row.addWidget(lbl_q)

        self.cmb_quality = NoScrollComboBox()
        self.cmb_quality.setFixedHeight(26)
        for opt in self.data.get("format_options", []):
            self.cmb_quality.addItem(opt["label"], opt["key"])

        if self.cmb_quality.count() > 0:
            self.cmb_quality.setCurrentIndex(0)

        bottom_row.addWidget(self.cmb_quality, stretch=1)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #28a745; font-size: 11px; font-weight: bold;")
        bottom_row.addWidget(self.lbl_status)

        self.btn_open_file = QPushButton("📁 Open")
        self.btn_open_file.setFixedHeight(24)
        self.btn_open_file.setStyleSheet("background-color: #28a745; font-size: 10px; padding: 2px 8px;")
        self.btn_open_file.setVisible(False)
        self.btn_open_file.clicked.connect(self.open_downloaded_file)
        bottom_row.addWidget(self.btn_open_file)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setStyleSheet(
            "background-color: #3d1c24; color: #ff4d6d; border-radius: 12px; font-weight: bold;"
        )
        self.btn_delete.clicked.connect(self.cleanup_and_delete)
        bottom_row.addWidget(self.btn_delete)

        info_layout.addLayout(bottom_row)
        layout.addLayout(info_layout, stretch=1)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self, event)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.is_selected = selected
        self.update_style()

    def update_style(self) -> None:
        if self.is_selected:
            self.setStyleSheet(CARD_SELECTED_QSS)
        else:
            self.setStyleSheet(CARD_DEFAULT_QSS)

    def mark_completed(self, file_path: str) -> None:
        self.is_completed = True
        self.saved_file_path = file_path
        self.lbl_status.setText("✔ Done")
        self.lbl_status.setStyleSheet("color: #28a745; font-size: 11px; font-weight: bold;")
        self.btn_open_file.setVisible(True)

    def open_downloaded_file(self) -> None:
        if self.saved_file_path and os.path.exists(self.saved_file_path):
            if sys.platform == "win32":
                os.startfile(os.path.normpath(self.saved_file_path))
            else:
                subprocess.Popen(["xdg-open", self.saved_file_path])

    def load_thumbnail_async(self) -> None:
        thumb_url = self.data.get("thumb_url")
        if not thumb_url:
            self.lbl_thumb.setText("No Preview")
            return

        self.thumb_loader = ThumbnailLoader(thumb_url)
        self.thumb_loader.loaded.connect(self._on_thumbnail_loaded)
        self.thumb_loader.start()

    def _on_thumbnail_loaded(self, pixmap: QPixmap) -> None:
        try:
            self.lbl_thumb.setPixmap(pixmap)
        except Exception:
            pass

    def cleanup_and_delete(self) -> None:
        if self.thumb_loader and self.thumb_loader.isRunning():
            self.thumb_loader.cancel()
            self.thumb_loader.wait(300)
        self.removed.emit(self)

    def get_selected_format(self) -> str:
        return self.cmb_quality.currentData()