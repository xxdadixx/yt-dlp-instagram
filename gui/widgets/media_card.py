import sys
from typing import Dict, Any, Optional

try:
    from PyQt6.QtCore import Qt, pyqtSignal, QSize
    from PyQt6.QtGui import QPixmap, QColor, QFont
    from PyQt6.QtWidgets import (
        QWidget,
        QFrame,
        QHBoxLayout,
        QVBoxLayout,
        QLabel,
        QCheckBox,
        QPushButton,
        QSizePolicy,
    )
except ImportError:

    class MagicSignal:
        def __init__(self):
            self._slots = []

        def connect(self, f):
            self._slots.append(f)

        def emit(self, *a, **kw):
            for s in self._slots:
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

    class QWidget:
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

    class QFrame(QWidget):
        pass

    class QHBoxLayout:
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

    class QVBoxLayout(QHBoxLayout):
        pass

    class QLabel(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)

        def setText(self, *a):
            pass

        def setPixmap(self, *a):
            pass

        def setScaledContents(self, *a):
            pass

        def setFixedSize(self, *a):
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

        def setFixedSize(self, *a):
            pass

        def setToolTip(self, *a):
            pass


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
    Features thumbnail preview, badges, duration/stats, selection checkbox,
    and action controls without emojis.
    """

    deleted = pyqtSignal() if "pyqtSignal" in globals() else MagicSignal()  # type: ignore
    selection_changed = pyqtSignal() if "pyqtSignal" in globals() else MagicSignal()  # type: ignore

    def __init__(self, item_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.item_data = dict(item_data)
        self.item_id = str(item_data.get("id") or item_data.get("shortcode") or "")
        self.shortcode = str(item_data.get("shortcode") or "")
        self.is_selected: bool = bool(item_data.get("selected", True))
        self.status: str = item_data.get("status", "ready")
        self.is_finished: bool = False
        self.thumb_loader: Optional[ThumbnailLoader] = None

        self.setObjectName("MediaCardFrame")
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        # 1. Selection Checkbox
        self.chk_select = QCheckBox(self)
        self.chk_select.setChecked(self.is_selected)
        if hasattr(self.chk_select, "stateChanged") and hasattr(
            self.chk_select.stateChanged, "connect"
        ):
            self.chk_select.stateChanged.connect(self._on_check_changed)
        layout.addWidget(self.chk_select)

        # 2. Thumbnail Preview Box
        self.lbl_thumb = QLabel(self)
        self.lbl_thumb.setObjectName("CardThumbnail")
        if hasattr(self.lbl_thumb, "setFixedSize"):
            self.lbl_thumb.setFixedSize(80, 80)
            self.lbl_thumb.setScaledContents(True)
        self.lbl_thumb.setStyleSheet(
            """
            QLabel#CardThumbnail {
                background-color: #121218;
                border: 1px solid #2d2d3d;
                border-radius: 6px;
            }
        """
        )
        layout.addWidget(self.lbl_thumb)

        # Asynchronously load thumbnail
        thumb_url = self.item_data.get("thumbnail_url")
        if thumb_url and ThumbnailLoader:
            self.thumb_loader = ThumbnailLoader(thumb_url, self)
            if hasattr(self.thumb_loader, "loaded") and self.thumb_loader.loaded:
                self.thumb_loader.loaded.connect(self._on_thumbnail_loaded)
            self.thumb_loader.start()

        # 3. Media Metadata Info (Vertical)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        # Title & Badges Row
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        # Type Badge
        badge_text = self.item_data.get("media_type", "reel").upper()
        self.lbl_badge = QLabel(badge_text, self)
        self.lbl_badge.setStyleSheet(
            """
            background-color: #3b82f6;
            color: #ffffff;
            font-size: 10px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 4px;
        """
        )
        title_row.addWidget(self.lbl_badge)

        # Title / Caption
        title_text = (
            self.item_data.get("title")
            or self.item_data.get("caption")
            or f"Reel {self.shortcode}"
        )
        if len(title_text) > 75:
            title_text = title_text[:72] + "..."
        self.lbl_title = QLabel(title_text, self)
        self.lbl_title.setStyleSheet(
            "color: #f8fafc; font-weight: 600; font-size: 13px;"
        )
        title_row.addWidget(self.lbl_title)
        title_row.addStretch()

        info_layout.addLayout(title_row)

        # Details Row: Username, Duration, Views, Likes
        details_row = QHBoxLayout()
        details_row.setSpacing(12)

        username = self.item_data.get("username", "")
        if username:
            lbl_user = QLabel(f"@{username}", self)
            lbl_user.setStyleSheet("color: #94a3b8; font-size: 11px;")
            details_row.addWidget(lbl_user)

        duration = float(self.item_data.get("duration") or 0.0)
        if duration > 0:
            mins = int(duration // 60)
            secs = int(duration % 60)
            lbl_dur = QLabel(f"Duration: {mins:02d}:{secs:02d}", self)
            lbl_dur.setStyleSheet("color: #94a3b8; font-size: 11px;")
            details_row.addWidget(lbl_dur)

        views = self.item_data.get("view_count", 0)
        if views:
            lbl_views = QLabel(f"{views:,} views", self)
            lbl_views.setStyleSheet("color: #94a3b8; font-size: 11px;")
            details_row.addWidget(lbl_views)

        likes = self.item_data.get("like_count", 0)
        if likes:
            lbl_likes = QLabel(f"{likes:,} likes", self)
            lbl_likes.setStyleSheet("color: #94a3b8; font-size: 11px;")
            details_row.addWidget(lbl_likes)

        details_row.addStretch()
        info_layout.addLayout(details_row)

        # Status Line
        self.lbl_status = QLabel(f"Status: {self.status.capitalize()}", self)
        self.lbl_status.setStyleSheet(
            "color: #38bdf8; font-size: 11px; font-weight: 500;"
        )
        info_layout.addWidget(self.lbl_status)

        layout.addLayout(info_layout, stretch=1)

        # 4. Action Button (Delete card)
        self.btn_delete = QPushButton(self)
        self.btn_delete.setObjectName("CardDeleteButton")
        if hasattr(self.btn_delete, "setToolTip"):
            self.btn_delete.setToolTip("Remove from queue")
        icon = get_icon("trash", "#ef4444", 16)
        if icon and hasattr(self.btn_delete, "setIcon"):
            self.btn_delete.setIcon(icon)
        else:
            self.btn_delete.setText("✕")
        if hasattr(self.btn_delete, "setFixedSize"):
            self.btn_delete.setFixedSize(30, 30)
        self.btn_delete.setStyleSheet(
            """
            QPushButton#CardDeleteButton {
                background: #242432;
                border: 1px solid #36364a;
                border-radius: 6px;
                padding: 4px;
            }
            QPushButton#CardDeleteButton:hover {
                background: #ef4444;
                border: 1px solid #dc2626;
            }
        """
        )
        self.btn_delete.clicked.connect(lambda: self.deleted.emit())
        layout.addWidget(self.btn_delete)

        self.setStyleSheet(
            """
            QFrame#MediaCardFrame {
                background: #1a1a24;
                border: 1px solid #2a2a3a;
                border-radius: 8px;
            }
            QFrame#MediaCardFrame:hover {
                border: 1px solid #3b82f6;
                background: #1e1e2c;
            }
        """
        )

    def _on_thumbnail_loaded(self, pixmap):
        if hasattr(self.lbl_thumb, "setPixmap"):
            self.lbl_thumb.setPixmap(pixmap)

    def _on_check_changed(self, state: int):
        self.is_selected = state == 2 or state is True
        self.item_data["selected"] = self.is_selected
        self.selection_changed.emit()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.item_data["selected"] = selected
        if hasattr(self.chk_select, "setChecked"):
            self.chk_select.setChecked(selected)

    def set_status(self, status: str):
        self.status = status
        if status == "finished":
            self.is_finished = True
            self.lbl_status.setText("Status: Completed")
            self.lbl_status.setStyleSheet(
                "color: #4ade80; font-size: 11px; font-weight: bold;"
            )
        elif status == "downloading":
            self.lbl_status.setText("Status: Downloading...")
            self.lbl_status.setStyleSheet(
                "color: #fbbf24; font-size: 11px; font-weight: bold;"
            )
        elif status == "error":
            self.lbl_status.setText("Status: Download Error")
            self.lbl_status.setStyleSheet(
                "color: #f87171; font-size: 11px; font-weight: bold;"
            )
        else:
            self.lbl_status.setText(f"Status: {status.capitalize()}")
            self.lbl_status.setStyleSheet(
                "color: #38bdf8; font-size: 11px; font-weight: 500;"
            )

    def get_item_data(self) -> Dict[str, Any]:
        return dict(self.item_data)
