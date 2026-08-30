"""
gui/widgets/url_chip_input.py - URL input controller and tab-embeddable chip list view.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.parser import parse_instagram_url
from gui.styles import MEDIA_TYPE_COLORS


class UrlChipItem(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, raw_url: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.raw_url = raw_url
        self._init_ui()

    def _init_ui(self) -> None:
        self.setObjectName("urlChipItem")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 8, 2)
        layout.setSpacing(10)

        meta = parse_instagram_url(self.raw_url)
        ttype = str(meta.get("type") or "LINK").upper()
        badge_style = MEDIA_TYPE_COLORS.get("POST")
        for prefix, style in MEDIA_TYPE_COLORS.items():
            if ttype.startswith(prefix):
                badge_style = style
                break

        self.badge_lbl = QLabel(ttype, self)
        self.badge_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self.badge_lbl.setStyleSheet(
            f"""
            background-color: {badge_style['bg']};
            color: {badge_style['fg']};
            border: 1px solid {badge_style['border']};
            border-radius: 4px;
            padding: 1px 6px;
            font-size: 7.5pt;
        """
        )
        layout.addWidget(self.badge_lbl)

        clean_text = (
            self.raw_url.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
        )
        self.url_lbl = QLabel(
            clean_text[:60] + ("..." if len(clean_text) > 60 else ""), self
        )
        self.url_lbl.setToolTip(self.raw_url)
        self.url_lbl.setFont(QFont("Segoe UI", 8))
        self.url_lbl.setStyleSheet("color: #E2E2EA; background: transparent;")
        layout.addWidget(self.url_lbl, 1)

        self.del_btn = QPushButton("✕", self)
        self.del_btn.setObjectName("ChipDeleteButton")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.setStyleSheet(
            """
            QPushButton#ChipDeleteButton {
                background: rgba(255, 255, 255, 0.04);
                color: #A0A0B2;
                border: 1px solid rgba(255, 255, 255, 0.06);
                font-size: 8pt;
                font-weight: bold;
                border-radius: 11px;
                padding: 0px;
            }
            QPushButton#ChipDeleteButton:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #EF4444,
                    stop: 1 #DC2626
                );
                color: #FFFFFF;
                border: 1px solid #FFA5A5;
            }
            QPushButton#ChipDeleteButton:pressed {
                background: #991B1B;
                color: #FECACA;
                border: 1px solid #7F1D1D;
                padding-top: 1px;
            }
        """
        )
        self.del_btn.clicked.connect(lambda: self.removed.emit(self.raw_url))
        layout.addWidget(self.del_btn)

        self.setStyleSheet(
            """
            QFrame#urlChipItem {
                background-color: #1A1A24;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QFrame#urlChipItem:hover {
                border: 1px solid rgba(225, 48, 108, 0.4);
                background-color: #222230;
            }
        """
        )


class URLChipInput(QObject := QWidget):
    """
    Manages URL input text entry and provides the embeddable chip list view for the tab container.
    """

    urls_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._urls: List[str] = []
        self._chip_widgets: Dict[str, UrlChipItem] = {}
        self._scroll_anim: Optional[QPropertyAnimation] = None

        self._init_input_bar()
        self._init_list_view()

    def _init_input_bar(self) -> None:
        """Constructs the top input bar widget."""
        self.input_widget = QWidget()
        layout = QHBoxLayout(self.input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.input_edit = QLineEdit(self.input_widget)
        self.input_edit.setPlaceholderText(
            "Paste Instagram links (Ctrl+V or Enter multiple links)..."
        )
        self.input_edit.setFixedHeight(36)
        self.input_edit.setStyleSheet(
            """
            QLineEdit {
                background-color: #16161F;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 6px 12px;
                color: #FFFFFF;
                font-size: 9pt;
            }
            QLineEdit:focus {
                border: 1px solid #E1306C;
                background-color: #1C1C28;
            }
        """
        )
        self.input_edit.returnPressed.connect(self._handle_manual_entry)
        self.input_edit.installEventFilter(self)
        layout.addWidget(self.input_edit, 1)

        self.btn_add = QPushButton("+ Add", self.input_widget)
        self.btn_add.setObjectName("AddUrlButton")
        self.btn_add.setFixedHeight(36)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.clicked.connect(self._handle_manual_entry)
        self.btn_add.setStyleSheet(
            """
            QPushButton#AddUrlButton {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #833AB4,
                    stop: 0.5 #E1306C,
                    stop: 1 #F56040
                );
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 0 18px;
                font-weight: 700;
                font-size: 8.5pt;
            }
            QPushButton#AddUrlButton:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #9546CD,
                    stop: 0.5 #EE3E7A,
                    stop: 1 #F77254
                );
                border: 1px solid #FFFFFF;
            }
            QPushButton#AddUrlButton:pressed {
                background: #B82556;
                padding-top: 2px;
            }
            QPushButton#AddUrlButton:disabled {
                background: #1A1822 !important;
                color: #4B4B5A !important;
                border: 1px solid rgba(255, 255, 255, 0.04) !important;
            }
        """
        )
        layout.addWidget(self.btn_add)

    def _init_list_view(self) -> None:
        """Constructs the tab-embeddable chip list view widget."""
        self.list_widget = QWidget()
        main_layout = QVBoxLayout(self.list_widget)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(6)

        # Tab Header Bar
        header_bar = QHBoxLayout()
        self.lbl_list_count = QLabel("URL Links (0 items)", self.list_widget)
        self.lbl_list_count.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_list_count.setStyleSheet("color: #FFFFFF;")
        header_bar.addWidget(self.lbl_list_count)
        header_bar.addStretch()

        self.btn_clear_all = QPushButton("Clear All Links", self.list_widget)
        self.btn_clear_all.setObjectName("DestructiveButton")
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.clicked.connect(self.clear)
        header_bar.addWidget(self.btn_clear_all)
        main_layout.addLayout(header_bar)

        # Scroll Area for URL Chips
        self.scroll_area = QScrollArea(self.list_widget)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(2, 2, 2, 2)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)

        self.scroll_area.setWidget(self.list_container)
        main_layout.addWidget(self.scroll_area, stretch=1)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if watched == self.input_edit and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                cb = QApplication.clipboard()
                if cb and self._process_raw_text(cb.text()):
                    return True
        return super().eventFilter(watched, event)

    def _handle_manual_entry(self) -> None:
        text = self.input_edit.text().strip()
        if text:
            self._process_raw_text(text)
            self.input_edit.clear()

    def _process_raw_text(self, text: str) -> bool:
        if not text:
            return False

        ig_regex = re.compile(
            r'https?://(?:www\.)?instagram\.com/[^\s"\'<>]+', re.IGNORECASE
        )
        matches = ig_regex.findall(text)
        tokens = matches if matches else re.split(r"[\r\n\t,;\s]+", text.strip())
        found_any = False

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            clean_url = re.sub(r"([?&])img_index=\d+(&?)", r"\1\2", token).rstrip("?&#")
            if clean_url in self._urls:
                continue
            self._add_chip(clean_url)
            found_any = True

        if found_any:
            self.input_edit.clear()
            self._sync_state()
            self.smooth_scroll_to_bottom()
            return True
        return False

    def _add_chip(self, url: str) -> None:
        chip = UrlChipItem(url, self.list_container)
        chip.removed.connect(self.remove_url)
        self._urls.append(url)
        self._chip_widgets[url] = chip
        self.list_layout.insertWidget(self.list_layout.count() - 1, chip)

    def add_url_chip(self, url: str) -> None:
        self._process_raw_text(url)

    def remove_url(self, url: str) -> None:
        if url in self._urls:
            self._urls.remove(url)
        if url in self._chip_widgets:
            widget = self._chip_widgets.pop(url)
            self.list_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._sync_state()

    def _sync_state(self) -> None:
        count = len(self._urls)
        self.lbl_list_count.setText(f"URL Links ({count} items)")
        self.urls_changed.emit()

    def smooth_scroll_to_bottom(self) -> None:
        """Animates smooth scrolling to the newest link entry."""
        v_bar = self.scroll_area.verticalScrollBar()
        if not v_bar:
            return
        target_val = v_bar.maximum()
        self._scroll_anim = QPropertyAnimation(v_bar, b"value", self)
        self._scroll_anim.setDuration(350)
        self._scroll_anim.setStartValue(v_bar.value())
        self._scroll_anim.setEndValue(target_val)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.start()

    def get_targets(self) -> List[str]:
        unsubmitted = self.input_edit.text().strip()
        if unsubmitted:
            self._process_raw_text(unsubmitted)
        return list(self._urls)

    def get_urls(self) -> List[str]:
        return self.get_targets()

    def count(self) -> int:
        return len(self._urls)

    def clear(self) -> None:
        for url in list(self._urls):
            self.remove_url(url)
        self.input_edit.clear()
        self._sync_state()


UrlChipInput = URLChipInput
URLChip = UrlChipItem
