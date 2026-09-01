"""
gui/widgets/media_card.py - Instagram-styled Media Card with scaled high-DPI thumbnails,
specular mouse illumination, and robust QThreadPool thumbnail lifecycle management.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import (
    QByteArray,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    pyqtSignal,
    QPropertyAnimation,
    pyqtProperty,
    QEasingCurve,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
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
    """Floating enlarged thumbnail preview popup with Liquid Glass rim and drop shadow."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
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
    """
    Liquid Glass 3D Thumbnail Pod (Google M3 Principles):
    - Sub-pixel QPainterPath clipping with dual-layer border strokes.
    - Specular cursor-reactive lighting.
    - Lerp-interpolated ambient pink/purple bloom shadow.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._raw_pixmap: Optional[QPixmap] = None
        self._rendered_pixmap: Optional[QPixmap] = None
        self._preview_popup: Optional[ThumbnailHoverPopup] = None

        self.setFixedSize(84, 84)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self._hover_progress: float = 0.0
        self._cursor_pos: QPointF = QPointF(-100.0, -100.0)

        # Ambient chromatic occlusion shadow
        self._elev_shadow = QGraphicsDropShadowEffect(self)
        self._elev_shadow.setBlurRadius(16)
        self._elev_shadow.setOffset(0, 4)
        self._elev_shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(self._elev_shadow)

        # Hover property animation
        self._anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @pyqtProperty(float)
    def hoverProgress(self) -> float:
        return self._hover_progress

    @hoverProgress.setter
    def hoverProgress(self, val: float) -> None:
        self._hover_progress = val
        blur = 16.0 + (10.0 * val)
        if val > 0.05:
            # Shift from dark drop shadow to Instagram crimson ambient bloom
            self._elev_shadow.setColor(QColor(225, 48, 108, int(130 * val)))
        else:
            self._elev_shadow.setColor(QColor(0, 0, 0, 160))
        self._elev_shadow.setBlurRadius(blur)
        self.update()

    def set_thumbnail_pixmap(self, pixmap: QPixmap) -> None:
        self._raw_pixmap = pixmap
        if not pixmap or pixmap.isNull():
            self._rendered_pixmap = None
            self.update()
            return

        target_size = 84
        scaled = pixmap.scaled(
            target_size,
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, (scaled.width() - target_size) // 2)
        crop_y = max(0, (scaled.height() - target_size) // 2)
        cropped = scaled.copy(crop_x, crop_y, target_size, target_size)

        # Mask inside a smooth rounded container
        rounded = QPixmap(target_size, target_size)
        rounded.fill(Qt.GlobalColor.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, target_size, target_size), 12.0, 12.0)
        p.setClipPath(path)
        p.drawPixmap(0, 0, cropped)
        p.end()

        self._rendered_pixmap = rounded
        self.setText("")
        self.update()

    def enterEvent(self, event) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(1.0)
        self._anim.start()

        if self._raw_pixmap and not self._raw_pixmap.isNull():
            if not self._preview_popup:
                self._preview_popup = ThumbnailHoverPopup()
            self._preview_popup.set_preview_pixmap(self._raw_pixmap)
            global_pos = self.mapToGlobal(QPoint(self.width() + 14, -120))
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                if global_pos.x() + 290 > geom.right():
                    global_pos.setX(self.mapToGlobal(QPoint(0, 0)).x() - 300)
                if global_pos.y() + 370 > geom.bottom():
                    global_pos.setY(geom.bottom() - 375)
                if global_pos.y() < geom.top():
                    global_pos.setY(geom.top() + 10)
            self._preview_popup.move(global_pos)
            self._preview_popup.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._cursor_pos = QPointF(-100.0, -100.0)

        if self._preview_popup:
            self._preview_popup.hide()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._cursor_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._preview_popup:
                self._preview_popup.hide()
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 12.0, 12.0)

        # 1. Base Glass Tint / Image Content
        if self._rendered_pixmap:
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, self._rendered_pixmap)
            painter.restore()
        else:
            base_grad = QLinearGradient(0, 0, 0, h)
            base_grad.setColorAt(0.0, QColor(28, 24, 40, 220))
            base_grad.setColorAt(1.0, QColor(16, 14, 24, 240))
            painter.fillPath(path, base_grad)

            painter.setPen(QColor(148, 163, 184, 180))
            font = QFont("Segoe UI", 8, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "NO PREVIEW")

        # 2. Specular Surface Sheen
        sheen_grad = QLinearGradient(0, 0, w, h)
        sheen_grad.setColorAt(
            0.0, QColor(255, 255, 255, int(40 + 35 * self._hover_progress))
        )
        sheen_grad.setColorAt(
            0.45, QColor(255, 255, 255, int(10 * self._hover_progress))
        )
        sheen_grad.setColorAt(1.0, QColor(0, 0, 0, 60))
        painter.save()
        painter.setClipPath(path)
        painter.fillPath(path, sheen_grad)

        # Radial cursor specular reflection
        if self._hover_progress > 0 and self._cursor_pos.x() >= 0:
            specular = QRadialGradient(self._cursor_pos, 70.0)
            specular.setColorAt(
                0.0, QColor(255, 255, 255, int(75 * self._hover_progress))
            )
            specular.setColorAt(
                0.6, QColor(225, 48, 108, int(35 * self._hover_progress))
            )
            specular.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, specular)
        painter.restore()

        # 3. Specular Micro-Bevel Border
        border_grad = QLinearGradient(0, 0, w, h)
        alpha_top = int(60 + 120 * self._hover_progress)
        alpha_bot = int(20 + 40 * self._hover_progress)
        border_grad.setColorAt(0.0, QColor(255, 255, 255, alpha_top))
        border_grad.setColorAt(
            0.5, QColor(225, 48, 108, int(120 * self._hover_progress))
        )
        border_grad.setColorAt(1.0, QColor(255, 255, 255, alpha_bot))

        pen = QPen(border_grad, 1.2)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.end()


class MediaCard(QFrame):
    """
    Liquid Glass Media Card Container.
    Synthesizes Google M3 elevation with cursor-tracking specular illumination,
    smooth OutCubic property lerping, and high-DPI micro-bevel rendering.
    """

    card_clicked = pyqtSignal(object, Qt.KeyboardModifier)
    clicked = card_clicked
    selection_changed = pyqtSignal(bool)
    deleted = pyqtSignal(object)

    def __init__(self, media_item: dict, parent: Optional[QWidget] = None) -> None:
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

        # Specular and Hover Motion State
        self._hover_progress: float = 0.0
        self._cursor_pos: QPointF = QPointF(-200.0, -200.0)
        self._is_hovered: bool = False
        self._is_pressed: bool = False

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("LiquidMediaCard")
        self.setFixedHeight(104)

        # Ambient Atmospheric Occlusion Shadow
        self._tray_glow = QGraphicsDropShadowEffect(self)
        self._tray_glow.setOffset(0, 4)
        self._tray_glow.setBlurRadius(14)
        self._tray_glow.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(self._tray_glow)

        # Hover Interpolation Animation
        self._anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._init_ui()
        self._set_children_transparent()
        self.update_style()
        self._load_thumbnail()

    @pyqtProperty(float)
    def hoverProgress(self) -> float:
        return self._hover_progress

    @hoverProgress.setter
    def hoverProgress(self, val: float) -> None:
        self._hover_progress = val

        # Choreographed shadow bloom based on selection and hover progress
        if self._is_selected:
            blur = 16.0 + (8.0 * val)
            alpha = int(75 + (55 * val))
            self._tray_glow.setBlurRadius(blur)
            self._tray_glow.setColor(QColor(225, 48, 108, alpha))
        else:
            blur = 12.0 + (10.0 * val)
            alpha = int(100 + (40 * val)) if val > 0.05 else 100
            self._tray_glow.setBlurRadius(blur)
            self._tray_glow.setColor(
                QColor(131, 58, 180, int(60 * val))
                if val > 0.05
                else QColor(0, 0, 0, alpha)
            )
        self.update()

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

        self.lbl_thumb = Elevated3DThumbnail(self)
        self.lbl_thumb.clicked.connect(self.open_image_gallery)
        layout.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignVCenter)

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

        display_title = (
            raw_title[:72].rstrip() + "..." if len(raw_title) > 75 else raw_title
        )
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

        # Minimum 36x36px touch target bound compliant with M3 guidelines
        self.btn_delete = QPushButton("✕", self)
        self.btn_delete.setObjectName("CardDeleteButton")
        self.btn_delete.setFixedSize(36, 36)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setFont(self._get_app_font(size=10, bold=True))
        self.btn_delete.setStyleSheet(
            """
            QPushButton#CardDeleteButton {
                background-color: rgba(255, 255, 255, 0.04);
                color: #71717A;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 18px;
                padding: 0px;
                font-size: 13px;
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

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self._anim.stop()
        self._anim.setStartValue(self._hover_progress)
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._cursor_pos = QPointF(-200.0, -200.0)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._cursor_pos = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_pressed = True
            self.update()
            self.card_clicked.emit(self, event.modifiers())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_pressed:
            self._is_pressed = False
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        card_path = QPainterPath()
        card_path.addRoundedRect(rect, 14.0, 14.0)

        # 1. Base Layer: Deep Frosted Acrylic Fill with dynamic elevation
        base_grad = QLinearGradient(0, 0, 0, h)
        if self._is_pressed:
            base_grad.setColorAt(0.0, QColor(18, 16, 26, 235))
            base_grad.setColorAt(1.0, QColor(12, 11, 18, 245))
        elif self._is_selected:
            alpha_top = int(190 + (35 * self._hover_progress))
            alpha_bot = int(220 + (25 * self._hover_progress))
            base_grad.setColorAt(0.0, QColor(34, 26, 50, alpha_top))
            base_grad.setColorAt(1.0, QColor(20, 17, 32, alpha_bot))
        else:
            alpha_top = int(175 + (30 * self._hover_progress))
            alpha_bot = int(205 + (20 * self._hover_progress))
            base_grad.setColorAt(0.0, QColor(26, 24, 38, alpha_top))
            base_grad.setColorAt(1.0, QColor(15, 14, 22, alpha_bot))

        painter.fillPath(card_path, base_grad)

        # 2. Dynamic Specular Light (Interactive Cursor Tracking)
        if self._hover_progress > 0 and self._cursor_pos.x() >= 0:
            specular = QRadialGradient(self._cursor_pos, 180.0)
            if self._is_selected:
                specular.setColorAt(
                    0.0, QColor(255, 255, 255, int(45 * self._hover_progress))
                )
                specular.setColorAt(
                    0.4, QColor(225, 48, 108, int(30 * self._hover_progress))
                )
                specular.setColorAt(1.0, QColor(225, 48, 108, 0))
            else:
                specular.setColorAt(
                    0.0, QColor(255, 255, 255, int(35 * self._hover_progress))
                )
                specular.setColorAt(
                    0.5, QColor(131, 58, 180, int(20 * self._hover_progress))
                )
                specular.setColorAt(1.0, QColor(255, 255, 255, 0))

            painter.save()
            painter.setClipPath(card_path)
            painter.fillPath(card_path, specular)
            painter.restore()

        # 3. Micro-Bevel Border with Top-Lit Specular Edge
        border_grad = QLinearGradient(0, 0, w, h)
        if self._is_selected:
            alpha_edge = int(190 + (65 * self._hover_progress))
            border_grad.setColorAt(0.0, QColor(255, 110, 160, alpha_edge))
            border_grad.setColorAt(
                0.5, QColor(225, 48, 108, int(180 * self._hover_progress) + 60)
            )
            border_grad.setColorAt(1.0, QColor(131, 58, 180, 100))
            pen_width = 1.4 + (0.4 * self._hover_progress)
        else:
            alpha_top = int(45 + (85 * self._hover_progress))
            alpha_mid = int(15 + (45 * self._hover_progress))
            alpha_bot = int(5 + (20 * self._hover_progress))
            border_grad.setColorAt(0.0, QColor(255, 255, 255, alpha_top))
            border_grad.setColorAt(0.6, QColor(225, 48, 108, alpha_mid))
            border_grad.setColorAt(1.0, QColor(255, 255, 255, alpha_bot))
            pen_width = 1.0 + (0.3 * self._hover_progress)

        pen = QPen()
        pen.setBrush(border_grad)
        pen.setWidthF(pen_width)
        painter.setPen(pen)
        painter.drawPath(card_path)

        painter.end()

    def set_selected(self, selected: bool) -> None:
        if self._is_selected != selected:
            self._is_selected = selected
            self.item_data["selected"] = selected
            self.update_style()
            self.selection_changed.emit(self._is_selected)

    def toggle_selected(self) -> None:
        self.set_selected(not self._is_selected)

    def update_style(self) -> None:
        # Trigger hoverProgress setter to update drop shadow glow cleanly
        self.hoverProgress = self._hover_progress

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
        self._anim.stop()
        if hasattr(self, "lbl_thumb") and getattr(
            self.lbl_thumb, "_preview_popup", None
        ):
            self.lbl_thumb._preview_popup.hide()
            self.lbl_thumb._preview_popup.deleteLater()
            self.lbl_thumb._preview_popup = None

        if self.thumb_loader is not None:
            self.thumb_loader.cancel()
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
