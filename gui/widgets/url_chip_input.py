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
    """A compact, styled pill/chip displaying an Instagram URL with a remove button."""

    removed = pyqtSignal(str) if "pyqtSignal" in globals() else MagicSignal()  # type: ignore

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.setObjectName("URLChipPill")
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(6)

        display_text = self.url
        if len(display_text) > 42:
            display_text = display_text[:22] + "..." + display_text[-17:]

        self.lbl_text = QLabel(display_text, self)
        self.lbl_text.setToolTip(self.url)
        self.lbl_text.setStyleSheet(
            "color: #e2e8f0; font-size: 11px; font-weight: 500;"
        )
        layout.addWidget(self.lbl_text)

        self.btn_close = QPushButton("✕", self)
        self.btn_close.setObjectName("ChipCloseButton")
        if hasattr(self.btn_close, "setFixedSize"):
            self.btn_close.setFixedSize(16, 16)
        self.btn_close.setStyleSheet(
            """
            QPushButton#ChipCloseButton {
                background: transparent;
                color: #a0aec0;
                border: none;
                font-size: 10px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton#ChipCloseButton:hover {
                color: #ff6b6b;
            }
        """
        )
        self.btn_close.clicked.connect(lambda: self.removed.emit(self.url))
        layout.addWidget(self.btn_close)

        self.setStyleSheet(
            """
            QFrame#URLChipPill {
                background: rgba(74, 134, 232, 0.18);
                border: 1px solid rgba(74, 134, 232, 0.45);
                border-radius: 12px;
            }
            QFrame#URLChipPill:hover {
                background: rgba(74, 134, 232, 0.28);
                border: 1px solid #4a86e8;
            }
        """
        )


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
        """Adds a URL to the input textbox and list."""
        url = url.strip()
        if not url:
            return

        current_text = self.text_edit.toPlainText().strip()
        lines = [line.strip() for line in current_text.splitlines() if line.strip()]
        if url not in lines:
            if current_text:
                self.text_edit.setPlainText(current_text + "\n" + url)
            else:
                self.text_edit.setPlainText(url)

    def get_targets(self) -> List[str]:
        """Extracts all targets from the text input field."""
        raw_text = self.text_edit.toPlainText().strip()
        if not raw_text:
            return []

        tokens = re.split(r"[\r\n\t,]+", raw_text)
        targets: List[str] = []
        seen: Set[str] = set()

        for tok in tokens:
            t = tok.strip()
            if t and t not in seen:
                seen.add(t)
                targets.append(t)

        return targets

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
