"""
gui/widgets/media_card.py - Instagram-styled Media Card with scaled high-DPI thumbnails and hover previews.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QByteArray, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.image_viewer_dialog import ImageViewerDialog
from gui.widgets.thumbnail_loader import ThumbnailLoader

logger = logging.getLogger(__name__)

TYPE_STYLES = {
    "CAROUSEL": {
        "bg": "rgba(245, 96, 64, 0.16)",
        "color": "#F56040",
        "border": "rgba(245, 96, 64, 0.40)",
    },
    "REEL": {
        "bg": "rgba(225, 48, 108, 0.18)",
        "color": "#E1306C",
        "border": "rgba(225, 48, 108, 0.45)",
    },
    "VIDEO": {
        "bg": "rgba(168, 85, 247, 0.16)",
        "color": "#A855F7",
        "border": "rgba(168, 85, 247, 0.40)",
    },
    "IMAGE": {
        "bg": "rgba(56, 189, 248, 0.16)",
        "color": "#38BDF8",
        "border": "rgba(56, 189, 248, 0.40)",
    },
    "PHOTO": {
        "bg": "rgba(56, 189, 248, 0.16)",
        "color": "#38BDF8",
        "border": "rgba(56, 189, 248, 0.40)",
    },
    "STORY": {
        "bg": "rgba(236, 72, 153, 0.16)",
        "color": "#F472B6",
        "border": "rgba(236, 72, 153, 0.40)",
    },
}

STATUS_STYLES = {
    "ready": {
        "text": "READY",
        "bg": "rgba(255, 255, 255, 0.05)",
        "color": "#94A3B8",
        "border": "rgba(255, 255, 255, 0.12)",
    },
    "queued": {
        "text": "QUEUED",
        "bg": "rgba(252, 175, 69, 0.16)",
        "color": "#FCAF45",
        "border": "rgba(252, 175, 69, 0.45)",
    },
    "downloading": {
        "text": "DOWNLOADING",
        "bg": "rgba(225, 48, 108, 0.22)",
        "color": "#FF7597",
        "border": "rgba(225, 48, 108, 0.55)",
    },
    "finished": {
        "text": "COMPLETED",
        "bg": "rgba(16, 185, 129, 0.18)",
        "color": "#34D399",
        "border": "rgba(16, 185, 129, 0.50)",
    },
    "error": {
        "text": "FAILED",
        "bg": "rgba(239, 68, 68, 0.18)",
        "color": "#F87171",
        "border": "rgba(239, 68, 68, 0.50)",
    },
}


class ThumbnailHoverPopup(QWidget):
    """Floating enlarged thumbnail preview popup with Liquid Glass rim and deep drop shadow."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self.frame = QFrame(self)
        self.frame.setStyleSheet(
            """
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1A1728, stop:1 #100E1A);
                border: 1.5px solid rgba(225, 48, 108, 0.6);
                border-radius: 14px;
            }
        """
        )
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)

        self.lbl_image = QLabel(self.frame)
        self.lbl_image.setFixedSize(260, 340)
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setStyleSheet("border-radius: 10px; background-color: #0B0A11;")
        frame_layout.addWidget(self.lbl_image)

        layout.addWidget(self.frame)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 200))
        self.frame.setGraphicsEffect(shadow)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap and not pixmap.isNull():
            target_w, target_h = 260, 340
            scaled = pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop_x = max(0, (scaled.width() - target_w) // 2)
            crop_y = max(0, (scaled.height() - target_h) // 2)
            cropped = scaled.copy(crop_x, crop_y, target_w, target_h)

            rounded = QPixmap(target_w, target_h)
            rounded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(rounded)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, target_w, target_h), 10.0, 10.0)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, cropped)
            painter.end()

            self.lbl_image.setPixmap(rounded)


class Elevated3DThumbnail(QLabel):
    """Top Layer (Z=24px): 3D Elevated Thumbnail casting ambient occlusion shadows."""

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._raw_pixmap: Optional[QPixmap] = None
        self._preview_popup: Optional[ThumbnailHoverPopup] = None

        self.setFixedSize(82, 82)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText("...")
        self.setStyleSheet(
            """
            QLabel {
                background-color: #171522;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
                color: #64748B;
                font-size: 11px;
                font-weight: bold;
            }
        """
        )

        self._elev_shadow = QGraphicsDropShadowEffect(self)
        self._elev_shadow.setBlurRadius(16)
        self._elev_shadow.setOffset(0, 4)
        self._elev_shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(self._elev_shadow)

    def set_thumbnail_pixmap(self, pixmap: QPixmap) -> None:
        self._raw_pixmap = pixmap
        if not pixmap or pixmap.isNull():
            self.setText("NO IMG")
            return

        scaled = pixmap.scaled(
            82,
            82,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, (scaled.width() - 82) // 2)
        crop_y = max(0, (scaled.height() - 82) // 2)
        cropped = scaled.copy(crop_x, crop_y, 82, 82)

        rounded = QPixmap(82, 82)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 82, 82), 12.0, 12.0)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()

        self.setText("")
        self.setPixmap(rounded)

    def enterEvent(self, event) -> None:
        self._elev_shadow.setBlurRadius(22)
        self._elev_shadow.setOffset(0, 6)
        self._elev_shadow.setColor(QColor(225, 48, 108, 120))

        if self._raw_pixmap and not self._raw_pixmap.isNull():
            if not self._preview_popup:
                self._preview_popup = ThumbnailHoverPopup()

            self._preview_popup.set_preview_pixmap(self._raw_pixmap)
            global_pos = self.mapToGlobal(QPoint(self.width() + 16, -125))
            screen = QApplication.primaryScreen()
            if screen:
                screen_geom = screen.availableGeometry()
                if global_pos.x() + 290 > screen_geom.right():
                    global_pos.setX(self.mapToGlobal(QPoint(0, 0)).x() - 300)
                if global_pos.y() + 370 > screen_geom.bottom():
                    global_pos.setY(screen_geom.bottom() - 375)
                if global_pos.y() < screen_geom.top():
                    global_pos.setY(screen_geom.top() + 10)

            self._preview_popup.move(global_pos)
            self._preview_popup.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._elev_shadow.setBlurRadius(16)
        self._elev_shadow.setOffset(0, 4)
        self._elev_shadow.setColor(QColor(0, 0, 0, 160))
        if self._preview_popup:
            self._preview_popup.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._preview_popup:
                self._preview_popup.hide()
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class MediaCard(QFrame):
    """2.5D Liquid Glass Media Card Container with scaled high-DPI dimensions."""

    card_clicked = pyqtSignal(object, Qt.KeyboardModifier)
    clicked = card_clicked
    selection_changed = pyqtSignal(bool)
    deleted = pyqtSignal(object)

    def __init__(self, media_item: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.item_data: Dict[str, Any] = media_item or {}
        self._is_selected: bool = bool(self.item_data.get("selected", True))
        self.item_id: str = str(
            self.item_data.get("id")
            or self.item_data.get("shortcode")
            or self.item_data.get("url")
            or "item"
        )
        self.status: str = str(self.item_data.get("status", "ready"))
        self.thumb_loader: Optional[ThumbnailLoader] = None
        self._is_cleaned_up: bool = False

        self._cursor_pos: QPointF = QPointF(-100, -100)
        self._is_hovered: bool = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("LiquidMediaCard")
        self.setFixedHeight(104)

        self._tray_glow = QGraphicsDropShadowEffect(self)
        self._tray_glow.setOffset(0, 4)
        self.setGraphicsEffect(self._tray_glow)

        self._init_ui()
        self._set_children_transparent()
        self.update_style()
        self._load_thumbnail()

    @property
    def is_selected(self) -> bool:
        return self._is_selected

    @property
    def is_finished(self) -> bool:
        return self.status.lower() == "finished"

    def _get_app_font(
        self, size: int = 10, bold: bool = False, weight: Optional[QFont.Weight] = None
    ) -> QFont:
        font = QFont("Segoe UI Variable Display", max(9, int(size)))
        font.setFamilies(
            [
                "-apple-system",
                "SF Pro Display",
                "Segoe UI Variable Display",
                "Segoe UI",
                "sans-serif",
            ]
        )
        if weight is not None:
            font.setWeight(weight)
        elif bold:
            font.setWeight(QFont.Weight.Bold)
        return font

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(16)

        # 1. 3D Elevated Thumbnail (Scaled to 82x82)
        self.lbl_thumb = Elevated3DThumbnail(self)
        self.lbl_thumb.clicked.connect(self.open_image_gallery)
        layout.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 2. Middle Layer: Title + Details Row
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 2, 0, 2)
        details_layout.setSpacing(6)

        raw_title = str(self.item_data.get("title") or "")
        full_caption = str(self.item_data.get("caption") or raw_title)

        self.lbl_title = QLabel(self)
        self.lbl_title.setFont(self._get_app_font(size=10, weight=QFont.Weight.Medium))
        self.lbl_title.setStyleSheet(
            "color: #FFFFFF; background: transparent; border: none; font-size: 13.5px;"
        )

        if len(raw_title) > 75:
            display_title = raw_title[:72].rstrip() + "..."
        else:
            display_title = raw_title

        self.lbl_title.setText(display_title)
        if full_caption:
            self.lbl_title.setToolTip(full_caption)

        details_layout.addWidget(self.lbl_title)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(10)

        raw_username = str(self.item_data.get("username") or "instagram").strip()
        display_username = (
            f"@{raw_username}" if not raw_username.startswith("@") else raw_username
        )
        self.lbl_username = QLabel(display_username, self)
        self.lbl_username.setFont(self._get_app_font(size=10, bold=True))
        self.lbl_username.setStyleSheet(
            "color: #38BDF8; background: transparent; border: none; font-size: 13px;"
        )
        meta_row.addWidget(self.lbl_username)

        badge_type = str(self.item_data.get("media_type") or "MEDIA").upper()
        slides_count = len(self.item_data.get("slides") or [])
        badge_label_text = (
            f"{badge_type} ({slides_count})"
            if "CAROUSEL" in badge_type and slides_count > 0
            else badge_type
        )

        style_info = TYPE_STYLES.get(
            badge_type.split()[0],
            {
                "bg": "rgba(255, 255, 255, 0.08)",
                "color": "#E2E8F0",
                "border": "rgba(255, 255, 255, 0.15)",
            },
        )
        self.lbl_badge = QLabel(badge_label_text, self)
        self.lbl_badge.setFont(self._get_app_font(size=9, bold=True))
        self.lbl_badge.setStyleSheet(
            f"""
            QLabel {{
                background-color: {style_info['bg']};
                color: {style_info['color']};
                border: 1px solid {style_info['border']};
                border-radius: 6px;
                padding: 2px 9px;
                font-size: 11.5px;
            }}
        """
        )
        meta_row.addWidget(self.lbl_badge)

        likes = self.item_data.get("like_count") or 0
        views = self.item_data.get("view_count") or 0
        meta_parts = []
        if likes > 0:
            meta_parts.append(f"❤️ {likes:,}")
        if views > 0:
            meta_parts.append(f"👁️ {views:,}")
        meta_str = " • ".join(meta_parts) if meta_parts else f"ID: {self.item_id[:12]}"

        self.lbl_meta = QLabel(meta_str, self)
        self.lbl_meta.setFont(self._get_app_font(size=10))
        self.lbl_meta.setStyleSheet(
            "color: #94A3B8; background: transparent; border: none; font-size: 12.5px;"
        )
        meta_row.addWidget(self.lbl_meta)
        meta_row.addStretch()

        details_layout.addLayout(meta_row)
        layout.addLayout(details_layout, stretch=1)

        # 3. Status Capsule & Delete Button
        action_col = QHBoxLayout()
        action_col.setContentsMargins(0, 0, 0, 0)
        action_col.setSpacing(12)
        action_col.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.lbl_status = QLabel(self)
        self.lbl_status.setFont(self._get_app_font(size=9, bold=True))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(self.status)
        action_col.addWidget(self.lbl_status)

        self.btn_delete = QPushButton("✕", self)
        self.btn_delete.setObjectName("CardDeleteButton")
        self.btn_delete.setFixedSize(30, 30)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setFont(self._get_app_font(size=10, bold=True))
        self.btn_delete.setStyleSheet(
            """
            QPushButton#CardDeleteButton {
                background-color: rgba(255, 255, 255, 0.04);
                color: #71717A;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 15px;
                padding: 0px;
                font-size: 12px;
            }
            QPushButton#CardDeleteButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #EF4444, stop:1 #DC2626);
                color: #FFFFFF;
                border: 1px solid #FFA5A5;
            }
            QPushButton#CardDeleteButton:pressed {
                background: #991B1B;
                color: #FECACA;
            }
            QPushButton#CardDeleteButton:disabled {
                background-color: transparent !important;
                color: #383842 !important;
                border: 1px solid rgba(255, 255, 255, 0.02) !important;
            }
        """
        )
        self.btn_delete.clicked.connect(lambda: self.deleted.emit(self))
        action_col.addWidget(self.btn_delete)

        layout.addLayout(action_col)

    def _set_children_transparent(self) -> None:
        for label in (
            self.lbl_title,
            self.lbl_username,
            self.lbl_badge,
            self.lbl_meta,
            self.lbl_status,
        ):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._cursor_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        self.update_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self._cursor_pos = QPointF(-100, -100)
        self.update_style()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self, event.modifiers())
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        card_path = QPainterPath()
        card_path.addRoundedRect(rect, 14.0, 14.0)

        base_grad = QLinearGradient(0, 0, w, h)
        if self._is_selected:
            base_grad.setColorAt(0.0, QColor(32, 24, 46, 210))
            base_grad.setColorAt(1.0, QColor(20, 17, 30, 230))
        else:
            base_grad.setColorAt(0.0, QColor(24, 22, 34, 180))
            base_grad.setColorAt(1.0, QColor(14, 13, 20, 210))

        painter.fillPath(card_path, base_grad)

        if self._is_hovered and self._cursor_pos.x() >= 0:
            specular = QRadialGradient(self._cursor_pos, 180.0)
            specular_color = (
                QColor(255, 255, 255, 30)
                if not self._is_selected
                else QColor(255, 117, 151, 45)
            )
            specular.setColorAt(0.0, specular_color)
            specular.setColorAt(1.0, QColor(255, 255, 255, 0))

            painter.save()
            painter.setClipPath(card_path)
            painter.fillPath(card_path, specular)
            painter.restore()

        border_grad = QLinearGradient(0, 0, w, h)
        if self._is_selected:
            border_grad.setColorAt(0.0, QColor(255, 60, 120, 240))
            border_grad.setColorAt(0.5, QColor(225, 48, 108, 180))
            border_grad.setColorAt(1.0, QColor(131, 58, 180, 120))
            border_width = 1.6
        else:
            if self._is_hovered:
                border_grad.setColorAt(0.0, QColor(255, 255, 255, 90))
                border_grad.setColorAt(0.6, QColor(255, 255, 255, 30))
                border_grad.setColorAt(1.0, QColor(255, 255, 255, 10))
                border_width = 1.2
            else:
                border_grad.setColorAt(0.0, QColor(255, 255, 255, 45))
                border_grad.setColorAt(0.7, QColor(255, 255, 255, 15))
                border_grad.setColorAt(1.0, QColor(255, 255, 255, 5))
                border_width = 1.0

        painter.strokePath(card_path, painter.pen().color().fromRgb(0, 0, 0, 0))
        pen = painter.pen()
        pen.setBrush(border_grad)
        pen.setWidthF(border_width)
        painter.setPen(pen)
        painter.drawPath(card_path)

        painter.end()
        super().paintEvent(event)

    def set_selected(self, selected: bool) -> None:
        if self._is_selected != selected:
            self._is_selected = selected
            self.item_data["selected"] = selected
            self.update_style()
            self.selection_changed.emit(self._is_selected)

    def toggle_selected(self) -> None:
        self.set_selected(not self._is_selected)

    def update_style(self) -> None:
        if self._is_selected:
            self._tray_glow.setBlurRadius(22 if self._is_hovered else 16)
            self._tray_glow.setColor(
                QColor(225, 48, 108, 95 if self._is_hovered else 70)
            )
        else:
            self._tray_glow.setBlurRadius(14 if self._is_hovered else 10)
            self._tray_glow.setColor(QColor(0, 0, 0, 140 if self._is_hovered else 100))
        self.update()

    def set_status(self, status: str) -> None:
        self.status = status
        self.item_data["status"] = status
        cfg = STATUS_STYLES.get(status.lower(), STATUS_STYLES["ready"])
        self.lbl_status.setText(cfg["text"])
        self.lbl_status.setStyleSheet(
            f"""
            QLabel {{
                background-color: {cfg['bg']};
                color: {cfg['color']};
                border: 1px solid {cfg['border']};
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 700;
            }}
        """
        )
        if hasattr(self, "btn_delete"):
            self.btn_delete.setEnabled(status.lower() != "downloading")

    def _load_thumbnail(self) -> None:
        thumb_url = self.item_data.get("thumbnail_url")
        if not thumb_url:
            slides = self.item_data.get("slides", [])
            if slides and isinstance(slides[0], dict):
                thumb_url = slides[0].get("thumbnail_url")

        if not thumb_url:
            self.lbl_thumb.setText("NO IMG")
            return

        self.thumb_loader = ThumbnailLoader(thumb_url, self)
        self.thumb_loader.loaded.connect(self._set_thumbnail_pixmap)
        self.thumb_loader.start()

    def _set_thumbnail_pixmap(self, raw_bytes: bytes) -> None:
        pix = QPixmap()
        if not pix.loadFromData(QByteArray(raw_bytes)):
            self.lbl_thumb.setText("NO IMG")
            return
        self.lbl_thumb.set_thumbnail_pixmap(pix)

    def get_item_data(self) -> Dict[str, Any]:
        return self.item_data

    def cleanup(self) -> None:
        self._is_cleaned_up = True
        if hasattr(self, "lbl_thumb") and getattr(
            self.lbl_thumb, "_preview_popup", None
        ):
            self.lbl_thumb._preview_popup.hide()
            self.lbl_thumb._preview_popup.deleteLater()
            self.lbl_thumb._preview_popup = None

        if self.thumb_loader is not None:
            if self.thumb_loader.isRunning():
                self.thumb_loader.cancel()
                self.thumb_loader.quit()
                self.thumb_loader.wait(200)
            self.thumb_loader = None

    def open_image_gallery(self) -> None:
        slides = self.item_data.get("slides")
        images: List[str] = []

        if slides and isinstance(slides, list):
            for s in slides:
                if not s.get("is_video"):
                    u = (
                        s.get("download_url")
                        or s.get("thumbnail_url")
                        or s.get("display_url")
                    )
                    if (
                        u
                        and isinstance(u, str)
                        and u.startswith("http")
                        and u not in images
                    ):
                        images.append(u)

        if not images:
            single_img = (
                self.item_data.get("thumbnail_url")
                or self.item_data.get("download_url")
                or self.item_data.get("display_url")
            )
            if (
                single_img
                and isinstance(single_img, str)
                and single_img.startswith("http")
            ):
                images.append(single_img)

        if images:
            dlg = ImageViewerDialog(
                image_urls=images,
                title=str(
                    self.item_data.get("title") or self.item_data.get("caption") or ""
                ),
                parent=self.window(),
            )
            dlg.exec()
