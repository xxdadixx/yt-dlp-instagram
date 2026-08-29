"""
gui/widgets/media_card.py - High-end modern Media Card widget for inspected Instagram media items.
Features aspect-ratio-preserving vertical thumbnails, badges, rich metadata, safe lifecycle cleanup, and queue controls.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from gui.styles import MEDIA_TYPE_COLORS
except ImportError:
    MEDIA_TYPE_COLORS = {
        "STORY": {"bg": "#D946EF", "fg": "#FFFFFF"},
        "REEL": {"bg": "#8B5CF6", "fg": "#FFFFFF"},
        "CAROUSEL (IMAGE)": {"bg": "#0284C7", "fg": "#FFFFFF"},
        "CAROUSEL (VIDEO)": {"bg": "#2563EB", "fg": "#FFFFFF"},
        "CAROUSEL": {"bg": "#0284C7", "fg": "#FFFFFF"},
        "IMAGE": {"bg": "#0D9488", "fg": "#FFFFFF"},
        "VIDEO": {"bg": "#EA580C", "fg": "#FFFFFF"},
        "POST": {"bg": "#3B82F6", "fg": "#FFFFFF"},
        "HIGHLIGHT": {"bg": "#F59E0B", "fg": "#000000"},
        "AUDIO": {"bg": "#10B981", "fg": "#FFFFFF"},
    }

try:
    from core.parser import parse_instagram_url
except ImportError:

    def parse_instagram_url(u: str) -> Dict[str, Any]:
        if "/reel/" in u.lower() or "/reels/" in u.lower():
            return {"type": "reel", "valid": True}
        if "img_index" in u.lower():
            return {"type": "carousel", "valid": True}
        return {"type": "post", "valid": True}


try:
    from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
    from PyQt6.QtGui import (
        QBitmap,
        QBrush,
        QColor,
        QFont,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )
    from PyQt6.QtWidgets import (
        QCheckBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    # Headless runtime fallback mocks
    class MagicSignal:
        def __init__(self):
            self._slots = []

        def connect(self, f):
            if f not in self._slots:
                self._slots.append(f)

        def disconnect(self, f=None):
            if f is None:
                self._slots.clear()
            elif f in self._slots:
                self._slots.remove(f)

        def emit(self, *a, **kw):
            for s in list(self._slots):
                try:
                    s(*a, **kw)
                except Exception:
                    pass

    class QSize:
        def __init__(self, w=0, h=0):
            self.w, self.h = w, h

    class QPixmap:
        def __init__(self, *a):
            pass

        def loadFromData(self, d):
            return True

        def isNull(self):
            return False

        def scaled(self, *a, **kw):
            return self

        def width(self):
            return 76

        def height(self):
            return 102

        def copy(self, *a):
            return self

        def fill(self, *a):
            pass

    class QWidget:  # type: ignore
        def __init__(self, parent=None):
            pass

        def setStyleSheet(self, *a):
            pass

        def setObjectName(self, *a):
            pass

        def deleteLater(self):
            pass

        def setParent(self, p):
            pass

        def setVisible(self, *a):
            pass

        def setFixedSize(self, *a):
            pass

        def setCursor(self, *a):
            pass

    class QFrame(QWidget):
        class Shape:
            NoFrame = 0

        def setFrameShape(self, *a):
            pass

    class QVBoxLayout:
        def __init__(self, parent=None):
            pass

        def setContentsMargins(self, *a):
            pass

        def setSpacing(self, *a):
            pass

        def addWidget(self, *a, **kw):
            pass

        def addLayout(self, *a, **kw):
            pass

        def addStretch(self, *a):
            pass

        def count(self):
            return 0

    class QHBoxLayout(QVBoxLayout):
        pass

    class QLabel(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._text = text

        def setText(self, *a):
            pass

        def setPixmap(self, *a):
            pass

        def setScaledContents(self, *a):
            pass

        def setFixedSize(self, *a):
            pass

        def setAlignment(self, *a):
            pass

        def setToolTip(self, *a):
            pass

        def clear(self):
            pass

    class QCheckBox(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self.stateChanged = MagicSignal()

        def setChecked(self, *a):
            pass

        def isChecked(self):
            return True

    class QPushButton(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self.clicked = MagicSignal()

        def setText(self, *a):
            pass

        def setIcon(self, *a):
            pass

        def setIconSize(self, *a):
            pass

        def setFixedSize(self, *a):
            pass

        def setToolTip(self, *a):
            pass


logger = logging.getLogger(__name__)

try:
    from gui.icons import get_icon
except ImportError:

    def get_icon(name: str, color: str = "#ffffff", size: int = 18):
        return None


try:
    from gui.widgets.thumbnail_loader import ThumbnailLoader
except ImportError:
    ThumbnailLoader = None


class MediaCard(QFrame):
    """
    Card item representing an inspected media entity in the queue grid.
    Features:
    - Non-distorted, smooth vertical aspect-ratio thumbnail previews (76x102)
    - Apple-inspired translucent dark theme & badge typography
    - Duration, view count, like count, and creator metrics
    - Real-time download status lifecycle & queue controls
    - Safe background thread teardown and memory cleanup
    """

    deleted = pyqtSignal() if "pyqtSignal" in globals() else MagicSignal()  # type: ignore
    selection_changed = pyqtSignal() if "pyqtSignal" in globals() else MagicSignal()  # type: ignore

    THUMB_WIDTH = 76
    THUMB_HEIGHT = 102

    def __init__(self, item_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.item_data = dict(item_data)
        self.item_id = str(item_data.get("id") or item_data.get("shortcode") or "")
        self.shortcode = str(item_data.get("shortcode") or "")
        self.item_url: str = str(
            item_data.get("url")
            or (
                f"https://www.instagram.com/reel/{self.shortcode}/"
                if self.shortcode
                else ""
            )
        )
        parsed_target = parse_instagram_url(self.item_url)
        url_type = str(parsed_target.get("type") or "").lower()
        raw_type = str(item_data.get("media_type") or "").strip().upper()
        if raw_type:
            self.media_type = raw_type
        else:
            parsed_target = parse_instagram_url(self.item_url)
            self.media_type = str(parsed_target.get("type") or "POST").upper()

        self.item_data["media_type"] = self.media_type
        self.is_selected: bool = bool(item_data.get("selected", True))
        self.status: str = item_data.get("status", "ready")
        self.is_finished: bool = False
        self.thumb_loader: Optional[ThumbnailLoader] = None
        self._is_cleaned_up: bool = False

        self.setObjectName("MediaCardFrame")
        self.init_ui()

    def cleanup(self) -> None:
        """Safely stops background thumbnail loaders and disconnects slots to prevent memory leaks."""
        if self._is_cleaned_up:
            return
        self._is_cleaned_up = True

        if self.thumb_loader:
            try:
                if hasattr(self.thumb_loader, "loaded") and self.thumb_loader.loaded:
                    self.thumb_loader.loaded.disconnect(self._on_thumbnail_loaded)
            except Exception:
                pass
            if hasattr(self.thumb_loader, "cancel"):
                self.thumb_loader.cancel()
            if hasattr(self.thumb_loader, "wait"):
                try:
                    self.thumb_loader.wait(100)
                except Exception:
                    pass
            self.thumb_loader = None

        if hasattr(self, "chk_select") and hasattr(self.chk_select, "stateChanged"):
            try:
                self.chk_select.stateChanged.disconnect()
            except Exception:
                pass

        if hasattr(self, "btn_delete") and hasattr(self.btn_delete, "clicked"):
            try:
                self.btn_delete.clicked.disconnect()
            except Exception:
                pass

        if hasattr(self, "lbl_thumb") and hasattr(self.lbl_thumb, "clear"):
            try:
                self.lbl_thumb.clear()
            except Exception:
                pass

    def init_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 14, 10)
        main_layout.setSpacing(14)

        # 1. Selection Checkbox
        self.chk_select = QCheckBox(self)
        self.chk_select.setChecked(self.is_selected)
        if hasattr(self.chk_select, "stateChanged") and hasattr(
            self.chk_select.stateChanged, "connect"
        ):
            self.chk_select.stateChanged.connect(self._on_check_changed)
        main_layout.addWidget(self.chk_select)

        # 2. Aspect-Ratio Preserving Thumbnail Box
        self.lbl_thumb = QLabel(self)
        self.lbl_thumb.setObjectName("CardThumbnail")
        if hasattr(self.lbl_thumb, "setFixedSize"):
            self.lbl_thumb.setFixedSize(self.THUMB_WIDTH, self.THUMB_HEIGHT)
        if hasattr(self.lbl_thumb, "setScaledContents"):
            self.lbl_thumb.setScaledContents(False)
        if hasattr(self.lbl_thumb, "setAlignment"):
            if "Qt" in globals() and hasattr(Qt, "AlignmentFlag"):
                self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge_type = getattr(
            self, "media_type", str(self.item_data.get("media_type", "reel"))
        ).upper()
        self.lbl_thumb.setText(badge_type)
        self.lbl_thumb.setStyleSheet(
            """
            QLabel#CardThumbnail {
                background-color: #0e0e16;
                color: #475569;
                font-size: 10px;
                font-weight: 700;
                border: 1px solid #232334;
                border-radius: 8px;
                qproperty-alignment: AlignCenter;
            }
        """
        )
        main_layout.addWidget(self.lbl_thumb)

        # Start asynchronous thumbnail fetcher
        thumb_url = self.item_data.get("thumbnail_url")
        if thumb_url and ThumbnailLoader:
            self.thumb_loader = ThumbnailLoader(thumb_url, self)
            if hasattr(self.thumb_loader, "loaded") and self.thumb_loader.loaded:
                self.thumb_loader.loaded.connect(self._on_thumbnail_loaded)
            self.thumb_loader.start()

        # 3. Media Metadata Information Layout
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setSpacing(5)

        # --- Line 1: Header (Badge, Shortcode Tag & Title) ---
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        # Media Type Badge
        badge_style = self._get_badge_style(badge_type)
        self.lbl_badge = QLabel(badge_type, self)
        self.lbl_badge.setStyleSheet(badge_style)
        header_row.addWidget(self.lbl_badge)

        # Shortcode Tag Pill
        if self.shortcode:
            self.lbl_code = QLabel(f"#{self.shortcode}", self)
            self.lbl_code.setStyleSheet(
                """
                background-color: #1c1c2a;
                color: #94a3b8;
                font-family: 'SF Mono', Consolas, Monaco, monospace;
                font-size: 11px;
                font-weight: 500;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid #28283a;
            """
            )
            header_row.addWidget(self.lbl_code)

        # Caption / Title Text
        title_text = (
            self.item_data.get("title")
            or self.item_data.get("caption")
            or f"Instagram Reel {self.shortcode}"
        )
        display_title = title_text.replace("\n", " ").strip()
        if len(display_title) > 65:
            display_title = display_title[:62] + "..."

        self.lbl_title = QLabel(display_title, self)
        self.lbl_title.setToolTip(self.item_data.get("caption") or title_text)
        self.lbl_title.setStyleSheet(
            "color: #f8fafc; font-weight: 600; font-size: 13px; letter-spacing: 0.2px;"
        )
        header_row.addWidget(self.lbl_title)
        header_row.addStretch()

        info_layout.addLayout(header_row)

        # --- Line 2: Creator & Metrics Details ---
        meta_row = QHBoxLayout()
        meta_row.setSpacing(14)

        username = self.item_data.get("username", "")
        if username:
            self.lbl_user = QLabel(f"@{username}", self)
            self.lbl_user.setStyleSheet(
                "color: #60a5fa; font-weight: 600; font-size: 12px;"
            )
            meta_row.addWidget(self.lbl_user)

        duration = float(self.item_data.get("duration") or 0.0)
        if duration > 0:
            mins = int(duration // 60)
            secs = int(duration % 60)
            self.lbl_dur = QLabel(f"⏱ {mins:02d}:{secs:02d}", self)
            self.lbl_dur.setStyleSheet(
                "color: #94a3b8; font-size: 11px; font-weight: 500;"
            )
            meta_row.addWidget(self.lbl_dur)

        views = self.item_data.get("view_count", 0)
        if views:
            views_str = self._format_count(views)
            self.lbl_views = QLabel(f"👁 {views_str} views", self)
            self.lbl_views.setStyleSheet(
                "color: #94a3b8; font-size: 11px; font-weight: 500;"
            )
            meta_row.addWidget(self.lbl_views)

        likes = self.item_data.get("like_count", 0)
        if likes:
            likes_str = self._format_count(likes)
            self.lbl_likes = QLabel(f"♥ {likes_str} likes", self)
            self.lbl_likes.setStyleSheet(
                "color: #94a3b8; font-size: 11px; font-weight: 500;"
            )
            meta_row.addWidget(self.lbl_likes)

        meta_row.addStretch()
        info_layout.addLayout(meta_row)

        # --- Line 3: Direct URL Link ---
        url_row = QHBoxLayout()
        url_row.setSpacing(6)

        item_url = (
            self.item_data.get("url")
            or f"https://www.instagram.com/reel/{self.shortcode}/"
        )
        display_url = item_url
        if len(display_url) > 60:
            display_url = display_url[:35] + "..." + display_url[-18:]

        self.lbl_url = QLabel(
            f'<a href="{item_url}" style="color: #60a5fa; text-decoration: none;">🔗 {display_url}</a>',
            self,
        )
        if hasattr(self.lbl_url, "setOpenExternalLinks"):
            self.lbl_url.setOpenExternalLinks(True)
        if hasattr(self.lbl_url, "setTextInteractionFlags") and "Qt" in globals():
            self.lbl_url.setTextInteractionFlags(
                getattr(Qt.TextInteractionFlag, "TextBrowserInteraction", 1)
                | getattr(Qt.TextInteractionFlag, "TextSelectableByMouse", 1)
            )
        self.lbl_url.setToolTip(f"Instagram Link: {item_url}")
        self.lbl_url.setStyleSheet("font-size: 11px; font-weight: 500;")
        url_row.addWidget(self.lbl_url)
        url_row.addStretch()
        info_layout.addLayout(url_row)

        # --- Line 4: Real-Time Status Lifecycle ---
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.lbl_status = QLabel("● Ready to Download", self)
        self.lbl_status.setStyleSheet(
            "color: #38bdf8; font-size: 11px; font-weight: 600;"
        )
        status_row.addWidget(self.lbl_status)
        status_row.addStretch()

        info_layout.addLayout(status_row)

        main_layout.addLayout(info_layout, stretch=1)

        # 4. Action Controls (Delete Button)
        self.btn_delete = QPushButton(self)
        self.btn_delete.setObjectName("CardDeleteButton")
        if hasattr(self.btn_delete, "setToolTip"):
            self.btn_delete.setToolTip("Remove from download queue")
        if hasattr(self.btn_delete, "setFixedSize"):
            self.btn_delete.setFixedSize(32, 32)

        icon = get_icon("trash", "#f87171", 16)
        if icon and hasattr(self.btn_delete, "setIcon"):
            self.btn_delete.setIcon(icon)
            if hasattr(self.btn_delete, "setIconSize"):
                self.btn_delete.setIconSize(QSize(16, 16))
        else:
            self.btn_delete.setText("✕")

        self.btn_delete.setStyleSheet(
            """
            QPushButton#CardDeleteButton {
                background: #181824;
                color: #94a3b8;
                border: 1px solid #28283c;
                border-radius: 6px;
                padding: 4px;
            }
            QPushButton#CardDeleteButton:hover {
                background: #3b181c;
                color: #f87171;
                border: 1px solid #ef4444;
            }
            QPushButton#CardDeleteButton:pressed {
                background: #250f12;
            }
        """
        )
        self.btn_delete.clicked.connect(lambda: self.deleted.emit())
        main_layout.addWidget(self.btn_delete)

        # Initialize active selection styling
        self._update_selection_style()

    def _update_selection_style(self) -> None:
        """Updates border, glow, and background tint when card selection state changes."""
        if self.is_selected:
            self.setStyleSheet(
                """
                QFrame#MediaCardFrame {
                    background-color: #171c2e;
                    border: 1.5px solid #3b82f6;
                    border-radius: 10px;
                }
                QFrame#MediaCardFrame:hover {
                    background-color: #1c233a;
                    border: 1.5px solid #60a5fa;
                }
            """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#MediaCardFrame {
                    background-color: #14141e;
                    border: 1px solid #232332;
                    border-radius: 10px;
                }
                QFrame#MediaCardFrame:hover {
                    background-color: #181826;
                    border: 1px solid #3b82f6;
                }
            """
            )

    def mousePressEvent(self, event) -> None:
        """Toggles selection when clicking anywhere on the card outside interactive controls."""
        if (
            hasattr(event, "button")
            and "Qt" in globals()
            and hasattr(Qt, "MouseButton")
        ):
            if event.button() != getattr(Qt.MouseButton, "LeftButton", 1):
                super().mousePressEvent(event)
                return
        self.set_selected(not self.is_selected)
        self.selection_changed.emit()
        super().mousePressEvent(event)

    def _get_badge_style(self, badge_type: str) -> str:
        """Returns badge style mapped to MEDIA_TYPE_COLORS."""
        badge_key = badge_type.upper()
        style = MEDIA_TYPE_COLORS.get(badge_key, {"bg": "#3B82F6", "fg": "#FFFFFF"})
        return f"""
            QLabel {{
                background-color: {style['bg']};
                color: {style['fg']};
                font-size: 10px;
                font-weight: 700;
                padding: 2px 7px;
                border-radius: 4px;
                letter-spacing: 0.5px;
            }}
        """

    def _format_count(self, num: int) -> str:
        """Formats numbers into readable strings (e.g. 1.2M, 45.3K, 1,200)."""
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        if num >= 10_000:
            return f"{num / 1_000:.1f}K"
        return f"{num:,}"

    def _on_thumbnail_loaded(self, data: Any) -> None:
        """
        Processes thumbnail bytes on GUI main thread with smooth anti-aliased scaling,
        center-cropping to vertical ratio (76x102), and rounded corner mask.
        """
        if not data or self._is_cleaned_up:
            return

        try:
            pixmap = QPixmap()
            if isinstance(data, (bytes, bytearray)):
                if not pixmap.loadFromData(data):
                    return
            elif isinstance(data, QPixmap):
                pixmap = data

            if pixmap.isNull():
                return

            target_w = self.THUMB_WIDTH
            target_h = self.THUMB_HEIGHT

            # If PyQt6 full graphics engine is available
            if "QPainter" in globals() and "Qt" in globals():
                # 1. Scale with KeepAspectRatioByExpanding & SmoothTransformation
                aspect_mode = getattr(
                    Qt.AspectRatioMode, "KeepAspectRatioByExpanding", 2
                )
                trans_mode = getattr(Qt.TransformationMode, "SmoothTransformation", 1)
                scaled = pixmap.scaled(target_w, target_h, aspect_mode, trans_mode)

                # 2. Center crop
                cw = scaled.width()
                ch = scaled.height()
                crop_x = max(0, (cw - target_w) // 2)
                crop_y = max(0, (ch - target_h) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_w, target_h)

                # 3. Create smooth rounded rect pixmap
                rounded = QPixmap(target_w, target_h)
                rounded.fill(getattr(Qt.GlobalColor, "transparent", 0))

                painter = QPainter(rounded)
                if hasattr(QPainter.RenderHint, "Antialiasing"):
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                if hasattr(QPainter.RenderHint, "SmoothPixmapTransform"):
                    painter.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform, True
                    )

                path = QPainterPath()
                path.addRoundedRect(
                    0.0, 0.0, float(target_w), float(target_h), 8.0, 8.0
                )
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, cropped)
                painter.end()

                if hasattr(self.lbl_thumb, "setPixmap") and not self._is_cleaned_up:
                    self.lbl_thumb.setPixmap(rounded)
                    self.lbl_thumb.setText("")
            else:
                if hasattr(self.lbl_thumb, "setPixmap") and not self._is_cleaned_up:
                    self.lbl_thumb.setPixmap(pixmap)
                    self.lbl_thumb.setText("")
        except Exception as e:
            logger.debug(f"Thumbnail processing failed: {e}")

    def _on_check_changed(self, state: int) -> None:
        if self._is_cleaned_up:
            return
        self.is_selected = state == 2 or state is True
        self.item_data["selected"] = self.is_selected
        self._update_selection_style()
        self.selection_changed.emit()

    def set_selected(self, selected: bool) -> None:
        self.is_selected = selected
        self.item_data["selected"] = selected
        if hasattr(self.chk_select, "setChecked"):
            self.chk_select.setChecked(selected)
        self._update_selection_style()

    def set_status(self, status: str) -> None:
        self.status = status
        if status == "finished":
            self.is_finished = True
            self.lbl_status.setText("✔ Downloaded")
            self.lbl_status.setStyleSheet(
                "color: #4ade80; font-size: 11px; font-weight: 700;"
            )
        elif status == "downloading":
            self.lbl_status.setText("● Downloading...")
            self.lbl_status.setStyleSheet(
                "color: #fbbf24; font-size: 11px; font-weight: 700;"
            )
        elif status == "error":
            self.lbl_status.setText("✖ Download Error")
            self.lbl_status.setStyleSheet(
                "color: #f87171; font-size: 11px; font-weight: 700;"
            )
        else:
            self.lbl_status.setText(f"● {status.capitalize()}")
            self.lbl_status.setStyleSheet(
                "color: #38bdf8; font-size: 11px; font-weight: 600;"
            )

    def get_item_data(self) -> Dict[str, Any]:
        return dict(self.item_data)

    def update_badge(self, media_type: str) -> None:
        """Updates the media card badge text and dynamic color."""
        if hasattr(self, "lbl_badge") and self.lbl_badge:
            self.media_type = media_type.upper()
            self.lbl_badge.setText(self.media_type)
            self.lbl_badge.setStyleSheet(self._get_badge_style(self.media_type))
