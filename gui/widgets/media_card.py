"""
gui/widgets/media_card.py - Instagram-styled Media Card with interactive hover zoom previews.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.image_viewer_dialog import ImageViewerDialog
from gui.widgets.thumbnail_loader import ThumbnailLoader

logger = logging.getLogger(__name__)

STATUS_STYLES = {
    "ready": {
        "text": "READY",
        "bg": "rgba(255, 255, 255, 0.06)",
        "color": "#A0A0B2",
        "border": "rgba(255, 255, 255, 0.1)",
    },
    "queued": {
        "text": "QUEUED",
        "bg": "rgba(252, 175, 69, 0.18)",
        "color": "#FCAF45",
        "border": "rgba(252, 175, 69, 0.4)",
    },
    "downloading": {
        "text": "DOWNLOADING",
        "bg": "rgba(225, 48, 108, 0.22)",
        "color": "#FF7597",
        "border": "rgba(225, 48, 108, 0.5)",
    },
    "finished": {
        "text": "COMPLETED",
        "bg": "rgba(16, 185, 129, 0.22)",
        "color": "#6EE7B7",
        "border": "rgba(16, 185, 129, 0.5)",
    },
    "error": {
        "text": "FAILED",
        "bg": "rgba(239, 68, 68, 0.22)",
        "color": "#FF6B6B",
        "border": "rgba(239, 68, 68, 0.5)",
    },
}

logger = logging.getLogger(__name__)


class ThumbnailHoverPopup(QWidget):
    """
    Floating enlarged thumbnail preview popup with drop shadow and Instagram styling.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        # 1. Rounded Thumbnail Preview (Clickable Lightbox)
        self.lbl_thumb = QLabel("Loading...", self)
        self.lbl_thumb.setFixedSize(64, 64)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_thumb.setStyleSheet(
            "background-color: #21212B; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08); color: #71717A;"
        )
        self.lbl_thumb.setFont(self._get_app_font(size=8))
        self.lbl_thumb.mousePressEvent = lambda e: self.open_image_gallery()
        layout.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Details Column (Title & Metadata)
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(5)

        raw_title = str(
            self.item_data.get("title")
            or self.item_data.get("caption")
            or "Instagram Media"
        ).strip()
        display_title = raw_title.splitlines()[0] if raw_title else "Instagram Media"
        if len(display_title) > 95:
            display_title = display_title[:92] + "..."

        self.lbl_title = QLabel(display_title, self)
        self.lbl_title.setObjectName("CardTitle")
        self.lbl_title.setFont(self._get_app_font(size=10, bold=True))
        self.lbl_title.setStyleSheet("color: #FFFFFF;")
        details_layout.addWidget(self.lbl_title)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)

        # Username Tag
        raw_username = str(self.item_data.get("username") or "instagram").strip()
        display_username = (
            f"@{raw_username}" if not raw_username.startswith("@") else raw_username
        )
        self.lbl_username = QLabel(display_username, self)
        self.lbl_username.setObjectName("CardUsername")
        self.lbl_username.setFont(self._get_app_font(size=9, bold=True))
        self.lbl_username.setStyleSheet("color: #38BDF8;")
        meta_row.addWidget(self.lbl_username)

        # Media Type Capsule Badge
        badge_type = str(self.item_data.get("media_type") or "MEDIA").upper()
        self.lbl_badge = QLabel(badge_type, self)
        self.lbl_badge.setObjectName("CardBadge")
        self.lbl_badge.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_badge.setStyleSheet(
            "background-color: rgba(245, 96, 64, 0.18); color: #F56040; border-radius: 4px; padding: 2px 6px;"
        )
        meta_row.addWidget(self.lbl_badge)

        # Likes / Views Metadata
        likes = self.item_data.get("like_count") or 0
        views = self.item_data.get("view_count") or 0
        meta_parts = []
        if likes > 0:
            meta_parts.append(f"❤️ {likes:,}")
        if views > 0:
            meta_parts.append(f"👁️ {views:,}")
        meta_str = " • ".join(meta_parts) if meta_parts else f"ID: {self.item_id[:12]}"

        self.lbl_meta = QLabel(meta_str, self)
        self.lbl_meta.setObjectName("CardMeta")
        self.lbl_meta.setFont(self._get_app_font(size=9))
        self.lbl_meta.setStyleSheet("color: #94A3B8;")
        meta_row.addWidget(self.lbl_meta)
        meta_row.addStretch()

        details_layout.addLayout(meta_row)
        layout.addLayout(details_layout, stretch=1)

        # 3. Status Badge & Quick Remove Button
        action_col = QHBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(8)
        action_col.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.lbl_status = QLabel(self.status.upper(), self)
        self.lbl_status.setObjectName("StatusPillReady")
        self.lbl_status.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #A0A0B2; background-color: #262633; border-radius: 4px; padding: 3px 8px;"
        )
        action_col.addWidget(self.lbl_status)

        self.btn_delete = QPushButton("✕", self)
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setFont(self._get_app_font(size=9, bold=True))
        self.btn_delete.setStyleSheet(
            "QPushButton { background-color: transparent; color: #71717A; border: none; border-radius: 12px; }"
            "QPushButton:hover { background-color: rgba(239, 68, 68, 0.2); color: #EF4444; }"
        )
        self.btn_delete.clicked.connect(self.deleted.emit)
        action_col.addWidget(self.btn_delete)

        layout.addLayout(action_col)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                210,
                280,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cw, ch = scaled.width(), scaled.height()
            cropped = scaled.copy(
                max(0, (cw - 210) // 2), max(0, (ch - 280) // 2), 210, 280
            )
            self.lbl_image.setPixmap(cropped)


class HoverThumbnailLabel(QLabel):
    """
    Interactive thumbnail widget that shows a floating enlarged preview on hover.
    """

    def __init__(self, item_data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item_data: Dict[str, Any] = item_data or {}
        self.item_id: str = str(
            self.item_data.get("id")
            or self.item_data.get("shortcode")
            or self.item_data.get("url")
            or "item"
        )
        self.is_selected: bool = bool(self.item_data.get("selected", True))
        self.status: str = str(self.item_data.get("status", "ready"))
        self.thumb_loader: Optional[ThumbnailLoader] = None

        self.setObjectName("MediaCardFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._init_ui()
        self._load_thumbnail()

    def set_thumbnail_pixmap(self, pixmap: QPixmap) -> None:
        self._raw_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                44,
                54,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cw, ch = scaled.width(), scaled.height()
            cropped = scaled.copy(
                max(0, (cw - 44) // 2), max(0, (ch - 54) // 2), 44, 54
            )
            self.setPixmap(cropped)
            self.setText("")

    def enterEvent(self, event) -> None:
        if self._raw_pixmap and not self._raw_pixmap.isNull():
            if not self._preview_popup:
                self._preview_popup = ThumbnailHoverPopup()

            self._preview_popup.set_preview_pixmap(self._raw_pixmap)

            global_pos = self.mapToGlobal(QPoint(self.width() + 12, -110))
            screen = QApplication.primaryScreen()
            if screen:
                screen_geom = screen.availableGeometry()
                if global_pos.x() + 230 > screen_geom.right():
                    global_pos.setX(self.mapToGlobal(QPoint(0, 0)).x() - 240)
                if global_pos.y() + 300 > screen_geom.bottom():
                    global_pos.setY(screen_geom.bottom() - 305)
                if global_pos.y() < screen_geom.top():
                    global_pos.setY(screen_geom.top() + 10)

            self._preview_popup.move(global_pos)
            self._preview_popup.show()

        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._preview_popup:
            self._preview_popup.hide()
        super().leaveEvent(event)

    def hide_popup(self) -> None:
        if self._preview_popup:
            self._preview_popup.hide()
            self._preview_popup.deleteLater()
            self._preview_popup = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self, event.modifiers())
        super().mousePressEvent(event)


class MediaCard(QFrame):
    card_clicked = pyqtSignal(object, object)  # Emits (self, Qt.KeyboardModifier)
    deleted = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self, item_data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item_data: Dict[str, Any] = item_data or {}
        self.item_id: str = str(
            self.item_data.get("id")
            or self.item_data.get("shortcode")
            or self.item_data.get("url")
            or "item"
        )
        self.is_selected: bool = bool(self.item_data.get("selected", True))
        self.status: str = str(self.item_data.get("status", "ready"))
        self.thumb_loader: Optional[ThumbnailLoader] = None

        self.setObjectName("MediaCardFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._init_ui()
        self._update_selection_style()
        self._load_thumbnail()

    def _get_app_font(self, size: int = 9, bold: bool = False) -> QFont:
        valid_size = max(8, int(size))
        font = QFont("Segoe UI", valid_size)
        font.setFamilies(
            ["Segoe UI", "Leelawadee UI", "Tahoma", "Noto Sans Thai", "sans-serif"]
        )
        if bold:
            font.setWeight(QFont.Weight.Bold)
        return font

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        # 1. Rounded Thumbnail Preview (Clickable Lightbox)
        self.lbl_thumb = QLabel("Loading...", self)
        self.lbl_thumb.setFixedSize(64, 64)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_thumb.setStyleSheet(
            "background-color: #21212B; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08); color: #71717A;"
        )
        self.lbl_thumb.setFont(self._get_app_font(size=8))
        self.lbl_thumb.mousePressEvent = lambda e: self.open_image_gallery()
        layout.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Details Column (Title & Metadata)
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(5)

        raw_title = str(
            self.item_data.get("title")
            or self.item_data.get("caption")
            or "Instagram Media"
        ).strip()
        display_title = raw_title.splitlines()[0] if raw_title else "Instagram Media"
        if len(display_title) > 95:
            display_title = display_title[:92] + "..."

        self.lbl_title = QLabel(display_title, self)
        self.lbl_title.setObjectName("CardTitle")
        self.lbl_title.setFont(self._get_app_font(size=10, bold=True))
        self.lbl_title.setStyleSheet("color: #FFFFFF;")
        self.lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        details_layout.addWidget(self.lbl_title)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)

        # Username Tag
        raw_username = str(self.item_data.get("username") or "instagram").strip()
        display_username = (
            f"@{raw_username}" if not raw_username.startswith("@") else raw_username
        )
        self.lbl_username = QLabel(display_username, self)
        self.lbl_username.setObjectName("CardUsername")
        self.lbl_username.setFont(self._get_app_font(size=9, bold=True))
        self.lbl_username.setStyleSheet("color: #38BDF8;")
        self.lbl_username.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta_row.addWidget(self.lbl_username)

        # Media Type Capsule Badge
        badge_type = str(self.item_data.get("media_type") or "MEDIA").upper()
        self.lbl_badge = QLabel(badge_type, self)
        self.lbl_badge.setObjectName("CardBadge")
        self.lbl_badge.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_badge.setStyleSheet(
            "background-color: rgba(245, 96, 64, 0.18); color: #F56040; border-radius: 4px; padding: 2px 6px;"
        )
        self.lbl_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta_row.addWidget(self.lbl_badge)

        # Likes / Views Metadata
        likes = self.item_data.get("like_count") or 0
        views = self.item_data.get("view_count") or 0
        meta_parts = []
        if likes > 0:
            meta_parts.append(f"❤️ {likes:,}")
        if views > 0:
            meta_parts.append(f"👁️ {views:,}")
        meta_str = " • ".join(meta_parts) if meta_parts else f"ID: {self.item_id[:12]}"

        self.lbl_meta = QLabel(meta_str, self)
        self.lbl_meta.setObjectName("CardMeta")
        self.lbl_meta.setFont(self._get_app_font(size=9))
        self.lbl_meta.setStyleSheet("color: #94A3B8;")
        self.lbl_meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        meta_row.addWidget(self.lbl_meta)
        meta_row.addStretch()

        details_layout.addLayout(meta_row)
        layout.addLayout(details_layout, stretch=1)

        # 3. Status Badge & Quick Remove Button
        action_col = QHBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(8)
        action_col.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.lbl_status = QLabel(self.status.upper(), self)
        self.lbl_status.setObjectName("StatusPillReady")
        self.lbl_status.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #A0A0B2; background-color: #262633; border-radius: 4px; padding: 3px 8px;"
        )
        self.lbl_status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        action_col.addWidget(self.lbl_status)

        self.btn_delete = QPushButton("✕", self)
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setFont(self._get_app_font(size=9, bold=True))
        self.btn_delete.setStyleSheet(
            "QPushButton { background-color: transparent; color: #71717A; border: none; border-radius: 12px; }"
            "QPushButton:hover { background-color: rgba(239, 68, 68, 0.2); color: #EF4444; }"
        )
        self.btn_delete.clicked.connect(self.deleted.emit)
        action_col.addWidget(self.btn_delete)

        layout.addLayout(action_col)

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        # 1. Rounded Thumbnail Preview (Clickable Lightbox)
        self.lbl_thumb = QLabel(self)
        self.lbl_thumb.setFixedSize(64, 64)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_thumb.setStyleSheet(
            "background-color: #21212B; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.08); color: #71717A;"
        )
        self.lbl_thumb.setFont(self._get_app_font(size=8))
        self.lbl_thumb.setText("Loading...")
        self.lbl_thumb.mousePressEvent = lambda e: self.open_image_gallery()
        layout.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Details Column (Title & Metadata)
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(5)

        raw_title = str(
            self.item_data.get("title")
            or self.item_data.get("caption")
            or "Instagram Media"
        ).strip()
        display_title = raw_title.splitlines()[0] if raw_title else "Instagram Media"
        if len(display_title) > 95:
            display_title = display_title[:92] + "..."

        self.lbl_title = QLabel(display_title, self)
        self.lbl_title.setObjectName("CardTitle")
        self.lbl_title.setFont(self._get_app_font(size=10, bold=True))
        self.lbl_title.setStyleSheet("color: #FFFFFF;")
        details_layout.addWidget(self.lbl_title)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(8)

        # Username Tag
        raw_username = str(self.item_data.get("username") or "instagram").strip()
        display_username = (
            f"@{raw_username}" if not raw_username.startswith("@") else raw_username
        )
        self.lbl_username = QLabel(display_username, self)
        self.lbl_username.setObjectName("CardUsername")
        self.lbl_username.setFont(self._get_app_font(size=9, bold=True))
        self.lbl_username.setStyleSheet("color: #38BDF8;")
        meta_row.addWidget(self.lbl_username)

        # Media Type Capsule Badge
        badge_type = str(self.item_data.get("media_type") or "MEDIA").upper()
        self.lbl_badge = QLabel(badge_type, self)
        self.lbl_badge.setObjectName("CardBadge")
        self.lbl_badge.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_badge.setStyleSheet(
            "background-color: rgba(245, 96, 64, 0.18); color: #F56040; border-radius: 4px; padding: 2px 6px;"
        )
        meta_row.addWidget(self.lbl_badge)

        # Likes / Views Metadata
        likes = self.item_data.get("like_count") or 0
        views = self.item_data.get("view_count") or 0
        meta_parts = []
        if likes > 0:
            meta_parts.append(f"❤️ {likes:,}")
        if views > 0:
            meta_parts.append(f"👁️ {views:,}")
        meta_str = " • ".join(meta_parts) if meta_parts else f"ID: {self.item_id[:12]}"

        self.lbl_meta = QLabel(meta_str, self)
        self.lbl_meta.setObjectName("CardMeta")
        self.lbl_meta.setFont(self._get_app_font(size=9))
        self.lbl_meta.setStyleSheet("color: #94A3B8;")
        meta_row.addWidget(self.lbl_meta)
        meta_row.addStretch()

        details_layout.addLayout(meta_row)
        layout.addLayout(details_layout, stretch=1)

        # 3. Status Badge & Quick Remove Button
        action_col = QHBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(8)
        action_col.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.lbl_status = QLabel(self.status.upper(), self)
        self.lbl_status.setObjectName("StatusPillReady")
        self.lbl_status.setFont(self._get_app_font(size=8, bold=True))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #A0A0B2; background-color: #262633; border-radius: 4px; padding: 3px 8px;"
        )
        action_col.addWidget(self.lbl_status)

        self.btn_delete = QPushButton("✕", self)
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setFont(self._get_app_font(size=9, bold=True))
        self.btn_delete.setStyleSheet(
            "QPushButton { background-color: transparent; color: #71717A; border: none; border-radius: 12px; }"
            "QPushButton:hover { background-color: rgba(239, 68, 68, 0.2); color: #EF4444; }"
        )
        self.btn_delete.clicked.connect(self.deleted.emit)
        action_col.addWidget(self.btn_delete)

        layout.addLayout(action_col)

    def _on_check_toggled(self, checked: bool) -> None:
        self.is_selected = checked
        self.item_data["selected"] = checked
        self.selection_changed.emit()

    def _update_selection_style(self) -> None:
        if self.is_selected:
            self.setStyleSheet(
                """
                QFrame#MediaCardFrame {
                    background-color: #232334;
                    border: 1.5px solid #E1306C;
                    border-radius: 10px;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#MediaCardFrame {
                    background-color: #171720;
                    border: 1px solid #282836;
                    border-radius: 10px;
                }
                QFrame#MediaCardFrame:hover {
                    background-color: #1E1E2A;
                    border: 1px solid #3E3E52;
                }
                """
            )

    def _load_thumbnail(self) -> None:
        thumb_url = self.item_data.get("thumbnail_url")
        if not thumb_url:
            slides = self.item_data.get("slides", [])
            if slides and isinstance(slides[0], dict):
                thumb_url = slides[0].get("thumbnail_url")

        if not thumb_url:
            self.lbl_thumb.setText("No Image")
            return

        self.thumb_loader = ThumbnailLoader(thumb_url, self)
        self.thumb_loader.loaded.connect(self._set_thumbnail_pixmap)
        self.thumb_loader.start()

    def _set_thumbnail_pixmap(self, raw_bytes: bytes) -> None:
        pix = QPixmap()
        if not pix.loadFromData(QByteArray(raw_bytes)):
            self.lbl_thumb.setText("No Image")
            return

        scaled = pix.scaled(
            64,
            64,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, (scaled.width() - 64) // 2)
        crop_y = max(0, (scaled.height() - 64) // 2)
        cropped = scaled.copy(crop_x, crop_y, 64, 64)

        rounded = QPixmap(64, 64)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, 64.0, 64.0, 8.0, 8.0)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()

        self.lbl_thumb.setText("")
        self.lbl_thumb.setPixmap(rounded)

    def _open_lightbox(self) -> None:
        slides = self.item_data.get("slides")
        images = []
        if slides:
            images = [
                s.get("download_url") or s.get("thumbnail_url")
                for s in slides
                if s.get("download_url") or s.get("thumbnail_url")
            ]
        elif self.item_data.get("thumbnail_url"):
            images = [self.item_data["thumbnail_url"]]

        if images:
            dlg = ImageViewerDialog(
                images=images, initial_index=0, parent=self.window()
            )
            dlg.exec()

    def _on_thumbnail_loaded(self, data: bytes) -> None:
        if not data or getattr(self, "_is_cleaned_up", False):
            return
        pix = QPixmap()
        if pix.loadFromData(data) and not pix.isNull():
            self.lbl_thumb.set_thumbnail_pixmap(pix)

    def _apply_status_style(self, st: str) -> None:
        cfg = STATUS_STYLES.get(st, STATUS_STYLES["ready"])
        self.lbl_status.setText(cfg["text"])
        self.lbl_status.setStyleSheet(
            f"""
            background-color: {cfg['bg']};
            color: {cfg['color']};
            border: 1px solid {cfg['border']};
            border-radius: 4px;
            padding: 1px 6px;
            font-size: 7pt;
            font-weight: 700;
        """
        )

    def set_status(self, status: str) -> None:
        self.status = status
        self.item_data["status"] = status
        self.lbl_status.setText(status.upper())
        if status == "finished":
            self.lbl_status.setStyleSheet(
                "color: #10B981; background-color: rgba(16, 185, 129, 0.15); border-radius: 4px; padding: 3px 8px;"
            )
        elif status == "downloading":
            self.lbl_status.setStyleSheet(
                "color: #FF7597; background-color: rgba(225, 48, 108, 0.2); border-radius: 4px; padding: 3px 8px;"
            )
        elif status == "error":
            self.lbl_status.setStyleSheet(
                "color: #EF4444; background-color: rgba(239, 68, 68, 0.15); border-radius: 4px; padding: 3px 8px;"
            )
        else:
            self.lbl_status.setStyleSheet(
                "color: #A0A0B2; background-color: #262633; border-radius: 4px; padding: 3px 8px;"
            )

    def set_selected(self, selected: bool) -> None:
        self.is_selected = bool(selected)
        self.item_data["selected"] = self.is_selected
        self._update_selection_style()
        self.selection_changed.emit()

    def _on_toggle_select(self, state: int) -> None:
        self.is_selected = state == 2 or state is True
        self._update_selection_style()
        self.selection_changed.emit()

    def get_item_data(self) -> Dict[str, Any]:
        return self.item_data

    def cleanup(self) -> None:
        if self.thumb_loader and self.thumb_loader.isRunning():
            self.thumb_loader.wait(200)

    def open_image_gallery(self) -> None:
        slides = self.item_data.get("slides")
        images: List[str] = []

        if slides and isinstance(slides, list):
            for s in slides:
                if not s.get("is_video"):
                    u = s.get("download_url") or s.get("thumbnail_url") or s.get("display_url")
                    if u and isinstance(u, str) and u.startswith("http") and u not in images:
                        images.append(u)

        if not images:
            single_img = (
                self.item_data.get("thumbnail_url")
                or self.item_data.get("download_url")
                or self.item_data.get("display_url")
            )
            if single_img and isinstance(single_img, str) and single_img.startswith("http"):
                images.append(single_img)

        if images:
            dlg = ImageViewerDialog(
                image_urls=images,
                title=str(self.item_data.get("title") or self.item_data.get("caption") or ""),
                parent=self.window(),
            )
            dlg.exec()
