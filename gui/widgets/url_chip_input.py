import re
from typing import List, Set, Optional

try:
    from PyQt6.QtCore import Qt, pyqtSignal, QSize
    from PyQt6.QtGui import QFont, QColor, QKeySequence
    from PyQt6.QtWidgets import (
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QPlainTextEdit,
        QLabel,
        QPushButton,
        QFrame,
        QScrollArea,
        QSizePolicy,
    )
except ImportError:

    class QWidget:
        def __init__(self, parent=None):
            pass

        def setStyleSheet(self, *a):
            pass

        def setObjectName(self, *a):
            pass

        def setMinimumHeight(self, *a):
            pass

        def setMaximumHeight(self, *a):
            pass

        def setSizePolicy(self, *a):
            pass

    class QFrame(QWidget):
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

        def setText(self, *a):
            pass

    class QPushButton(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self.clicked = MagicSignal()

        def setText(self, *a):
            pass

    class QPlainTextEdit(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._text = ""

        def setPlaceholderText(self, t):
            pass

        def toPlainText(self):
            return self._text

        def setPlainText(self, t):
            self._text = t

        def clear(self):
            self._text = ""

        def appendPlainText(self, t):
            self._text += "\n" + t

    class MagicSignal:
        def connect(self, f):
            pass

        def emit(self, *a):
            pass


try:
    from gui.icons import get_icon
except ImportError:

    def get_icon(name: str, color: str = "#ffffff", size: int = 18):
        return None


class URLChip(QFrame):
    """A compact, styled pill/chip displaying an Instagram URL with category badge and remove button."""

    removed = pyqtSignal(str) if "pyqtSignal" in globals() else MagicSignal()  # type: ignore

    def __init__(self, url: str, category: str = "", parent=None):
        super().__init__(parent)
        self.url = url
        self.category = category
        self.setObjectName("URLChipPill")
        self.init_ui()

    def _format_chip_details(self, u: str) -> tuple[str, str, str]:
        """Returns (badge_text, display_label, badge_color)."""
        try:
            info = parse_instagram_url(u)
            ttype = info.get("type", "url").upper().replace("_", " ")
            user = info.get("username")
            code = info.get("shortcode")
            tid = info.get("target_id")

            if "REELS" in ttype or ttype == "PROFILE REELS":
                badge = "REELS"
                color = "#4f46e5"
                label = f"@{user} (Reels)" if user else u
            elif ttype == "PROFILE":
                badge = "PROFILE"
                color = "#059669"
                label = f"@{user} (Profile)" if user else u
            elif ttype == "REEL":
                badge = "REEL"
                color = "#6366f1"
                label = f"#{code} (Reel)" if code else u
            elif ttype in ("POST", "TV"):
                badge = "POST"
                color = "#3b82f6"
                label = f"#{code} (Post)" if code else u
            elif ttype == "STORY":
                badge = "STORY"
                color = "#e11d48"
                label = f"@{user} (Story)" if user else u
            elif ttype == "HIGHLIGHT":
                badge = "HIGHLIGHT"
                color = "#d97706"
                label = f"#{tid} (Highlight)" if tid else u
            elif ttype == "AUDIO":
                badge = "AUDIO"
                color = "#0284c7"
                label = f"#{tid} (Audio)" if tid else u
            else:
                badge = "URL"
                color = "#64748b"
                label = u

            if len(label) > 42:
                label = label[:22] + "..." + label[-16:]

            return badge, label, color
        except Exception:
            return "URL", u[:30], "#64748b"

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        badge_text, display_text, badge_bg = self._format_chip_details(self.url)
        if self.category:
            badge_text = self.category.upper()

        # Category Badge
        self.lbl_badge = QLabel(badge_text, self)
        self.lbl_badge.setStyleSheet(
            f"""
            background-color: {badge_bg};
            color: #ffffff;
            font-size: 9px;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 4px;
            letter-spacing: 0.3px;
        """
        )
        layout.addWidget(self.lbl_badge)

        self.lbl_text = QLabel(display_text, self)
        self.lbl_text.setToolTip(f"[{badge_text}] {self.url}")
        self.lbl_text.setStyleSheet(
            "color: #e2e8f0; font-size: 11px; font-weight: 500;"
        )
        layout.addWidget(self.lbl_text)


class URLChipInput(QWidget):
    """
    Multi-line URL input widget for Instagram URLs.
    Supports manual typing, multi-line pasting, auto-clipboard injection,
    and extraction of all clean targets.
    """

    urls_changed = pyqtSignal() if "pyqtSignal" in globals() else MagicSignal()  # type: ignore

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("URLChipInputContainer")
        self._chips: List[str] = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("URLInputBox")
        self.text_edit.setPlaceholderText(
            "Paste Instagram URLs here (e.g., https://www.instagram.com/username/reels/ or https://instagram.com/p/...)\n"
            "Supports multiple links (one per line, comma or space separated)..."
        )
        self.text_edit.setMinimumHeight(68)
        self.text_edit.setMaximumHeight(110)
        self.text_edit.setStyleSheet(
            """
            QPlainTextEdit#URLInputBox {
                background: #18181f;
                color: #f1f5f9;
                border: 1px solid #2d2d3a;
                border-radius: 8px;
                padding: 8px 12px;
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
                font-size: 13px;
                line-height: 1.4;
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
            }
            QPlainTextEdit#URLInputBox:focus {
                border: 1px solid #4a86e8;
                background: #1b1b24;
            }
        """
        )
        layout.addWidget(self.text_edit)

    def add_url_chip(self, url: str) -> None:
        """Adds a valid Instagram URL to the input textbox and categorizes it."""
        from core.parser import extract_instagram_urls

        valid_urls = extract_instagram_urls(url)
        if not valid_urls:
            return

        current_text = self.text_edit.toPlainText().strip()
        lines = [line.strip() for line in current_text.splitlines() if line.strip()]
        new_added = False
        for u in valid_urls:
            if u not in lines:
                lines.append(u)
                new_added = True

        if new_added:
            self._is_internal_updating = True
            self.text_edit.setPlainText(chr(10).join(lines))
            self._is_internal_updating = False
            self._rebuild_chips()
            self.urls_changed.emit()

    def get_targets(self) -> List[str]:
        """
        Extracts all valid Instagram targets from the text input field.
        Filters out non-Instagram text, arbitrary comments, or invalid entries.
        """
        raw_text = self.text_edit.toPlainText().strip()
        if not raw_text:
            return []

        from core.parser import extract_instagram_urls

        return extract_instagram_urls(raw_text)

    def clear(self) -> None:
        """Clears the text input and resets state."""
        self.text_edit.clear()
        self._chips.clear()

    def set_text(self, text: str) -> None:
        """Sets raw text content."""
        self.text_edit.setPlainText(text)

    def toPlainText(self) -> str:
        """Returns the raw input text."""
        return self.text_edit.toPlainText()
