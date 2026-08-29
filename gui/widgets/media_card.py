"""
gui/widgets/media_card.py - Instagram-styled Media Card with interactive hover zoom previews.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.styles import MEDIA_TYPE_COLORS

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.container = QFrame(self)
        self.container.setStyleSheet(
            """
            QFrame {
                background-color: #14141E;
                border: 1.5px solid #E1306C;
                border-radius: 12px;
            }
        """
        )

        # Drop shadow effect
        shadow = QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(4, 4, 4, 4)

        self.lbl_image = QLabel(self.container)
        self.lbl_image.setFixedSize(210, 280)
        self.lbl_image.setStyleSheet("background-color: #0E0E14; border-radius: 8px;")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.lbl_image)

        layout.addWidget(self.container)

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

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(44, 54)
        self.setStyleSheet(
            "background-color: #0E0E14; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.08);"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self._raw_pixmap: Optional[QPixmap] = None
        self._preview_popup: Optional[ThumbnailHoverPopup] = None

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


class MediaCard(QFrame):
    deleted = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self, item_data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item_data = dict(item_data)
        self.item_id = str(item_data.get("id") or item_data.get("shortcode") or "")
        self.shortcode = str(item_data.get("shortcode") or "")
        self.is_selected = True
        self.is_finished = False
        self.setMouseTracking(True)
        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("MediaCardCapsule")
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 12, 4)
        layout.setSpacing(12)

        # 1. Checkbox
        self.chk_select = QCheckBox(self)
        self.chk_select.setChecked(self.is_selected)
        self.chk_select.stateChanged.connect(self._on_toggle_select)
        layout.addWidget(self.chk_select)

        # 2. Interactive Hover Zoom Thumbnail
        self.lbl_thumb = HoverThumbnailLabel(self)
        self._load_thumbnail()
        layout.addWidget(self.lbl_thumb)

        # 3. Stacked Metadata Info
        info_stack = QVBoxLayout()
        info_stack.setSpacing(3)
        info_stack.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Line 1: Type Badge + Title
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        mtype = str(
            self.item_data.get("media_type") or self.item_data.get("type") or "POST"
        ).upper()
        badge_style = MEDIA_TYPE_COLORS.get("POST")
        for prefix, style in MEDIA_TYPE_COLORS.items():
            if mtype.startswith(prefix):
                badge_style = style
                break

        self.lbl_type = QLabel(mtype, self)
        self.lbl_type.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self.lbl_type.setStyleSheet(
            f"""
            background-color: {badge_style['bg']};
            color: {badge_style['fg']};
            border: 1px solid {badge_style['border']};
            border-radius: 4px;
            padding: 1px 6px;
            font-size: 7.5pt;
        """
        )
        top_row.addWidget(self.lbl_type)

        title = self.item_data.get("title") or f"Instagram Media #{self.shortcode}"
        display_title = title.replace("\n", " ").strip()
        if len(display_title) > 60:
            display_title = display_title[:57] + "..."

        self.lbl_title = QLabel(display_title, self)
        self.lbl_title.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.lbl_title.setStyleSheet("color: #FFFFFF; font-size: 9pt;")
        top_row.addWidget(self.lbl_title, 1)

        info_stack.addLayout(top_row)

        # Line 2: Subtle Path + Status Pill
        bot_row = QHBoxLayout()
        bot_row.setSpacing(8)

        url_str = self.item_data.get("url") or ""
        clean_url = url_str.replace("https://www.", "").replace("https://", "")
        self.lbl_url = QLabel(clean_url[:48], self)
        self.lbl_url.setFont(QFont("Segoe UI", 8))
        self.lbl_url.setStyleSheet("color: #A0A0B2; font-size: 8pt;")
        bot_row.addWidget(self.lbl_url)

        bot_row.addStretch()

        self.lbl_status = QLabel("READY", self)
        self.lbl_status.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._apply_status_style("ready")
        bot_row.addWidget(self.lbl_status)

        info_stack.addLayout(bot_row)
        layout.addLayout(info_stack, 1)

        # 4. Trash Button
        self.btn_del = QPushButton("✕", self)
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setStyleSheet(
            """
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: none;
                color: #A0A0B2;
                font-size: 9pt;
                font-weight: 500;
                border-radius: 11px;
            }
            QPushButton:hover {
                background: rgba(225, 48, 108, 0.3);
                color: #FF7597;
            }
        """
        )
        self.btn_del.clicked.connect(self.deleted.emit)
        layout.addWidget(self.btn_del)

        self._update_selection_style()

    def _update_selection_style(self) -> None:
        if self.is_selected:
            self.setStyleSheet(
                """
                QFrame#MediaCardCapsule {
                    background-color: #242436;
                    border: 1.5px solid #E1306C;
                    border-radius: 10px;
                }
            """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#MediaCardCapsule {
                    background-color: #1A1A24;
                    border: 1px solid rgba(255, 255, 255, 0.07);
                    border-radius: 10px;
                }
                QFrame#MediaCardCapsule:hover {
                    background-color: #20202E;
                    border: 1px solid rgba(225, 48, 108, 0.35);
                }
            """
            )

    def _load_thumbnail(self) -> None:
        t_url = self.item_data.get("thumbnail_url") or self.item_data.get("thumbnail")
        if t_url and str(t_url).startswith("http"):
            try:
                from gui.widgets.thumbnail_loader import ThumbnailLoader

                self.thumb_loader = ThumbnailLoader(str(t_url), self)
                self.thumb_loader.loaded.connect(self._on_thumbnail_loaded)
                self.thumb_loader.start()
            except Exception as e:
                logger.debug(f"Failed to initiate thumbnail loader: {e}")

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

    def set_status(self, st: str) -> None:
        self.is_finished = st == "finished"
        self._apply_status_style(st)
        if st == "finished":
            self.set_selected(False)

    def set_selected(self, s: bool) -> None:
        self.is_selected = s
        self.chk_select.blockSignals(True)
        self.chk_select.setChecked(s)
        self.chk_select.blockSignals(False)
        self._update_selection_style()
        self.selection_changed.emit()

    def _on_toggle_select(self, state: int) -> None:
        self.is_selected = state == 2 or state is True
        self._update_selection_style()
        self.selection_changed.emit()

    def get_item_data(self) -> Dict[str, Any]:
        data = dict(self.item_data)
        data["card_id"] = str(
            self.item_id or self.item_data.get("id") or self.item_data.get("shortcode")
        )
        return data

    def cleanup(self) -> None:
        if getattr(self, "_is_cleaned_up", False):
            return
        self._is_cleaned_up = True
        if hasattr(self, "lbl_thumb") and self.lbl_thumb:
            self.lbl_thumb.hide_popup()
        if hasattr(self, "thumb_loader") and self.thumb_loader:
            try:
                self.thumb_loader.cancel()
                self.thumb_loader.wait(100)
            except Exception:
                pass
            self.thumb_loader = None
