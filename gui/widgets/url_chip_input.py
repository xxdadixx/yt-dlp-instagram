"""
gui/widgets/url_chip_input.py - URL Chip Deck with scaled high-DPI input controls and URL cards.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QSize,
    Qt,
    pyqtSignal,
    QRectF,
)
from PyQt6.QtGui import (
    QFont,
    QKeySequence,
    QLinearGradient,
    QPainterPath,
    QPen,
    QColor,
    QPainter,
)
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

from core.parser import extract_instagram_urls, parse_instagram_url
from gui.icons import get_icon


class URLGlassThumbnailPod(QFrame):
    """
    Frosted glass thumbnail and media-type badge pod.
    Combines optical acrylic backing with vector iconography and specular rim lighting.
    """

    def __init__(self, target_type: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.target_type = target_type.upper()
        self.setFixedSize(40, 40)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 9.0, 9.0)

        # 1. Frosted Backing Fill
        fill_grad = QLinearGradient(0, 0, 0, h)
        fill_grad.setColorAt(0.0, QColor(32, 28, 48, 200))
        fill_grad.setColorAt(1.0, QColor(18, 16, 26, 230))
        painter.fillPath(path, fill_grad)

        # 2. Specular Rim Stroke
        border_grad = QLinearGradient(0, 0, w, h)
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 90))
        border_grad.setColorAt(0.6, QColor(225, 48, 108, 60))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 20))
        painter.setPen(QPen(border_grad, 1.0))
        painter.drawPath(path)

        # 3. Ambient Media Glyph
        painter.save()
        painter.setClipPath(path)
        icon_color = "#E1306C" if "REEL" in self.target_type else "#38BDF8"
        icon_name = "search" if "PROFILE" in self.target_type else "link"
        icon = get_icon(icon_name, color=icon_color, size=18)
        if icon and not icon.isNull():
            pix = icon.pixmap(18, 18)
            painter.drawPixmap(int((w - 18) / 2), int((h - 18) / 2), pix)
        painter.restore()

        painter.end()


class URLItemCard(QFrame):
    deleted = pyqtSignal(str)

    def __init__(
        self, url: str, target_type: str = "MEDIA", parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.url = url.strip()
        self.target_type = target_type.upper()
        self.setObjectName("URLItemCard")
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._init_ui()

    def _get_badge_palette(self) -> tuple[str, str, str]:
        palette = {
            "PROFILE": (
                "rgba(56, 189, 248, 0.15)",
                "rgba(56, 189, 248, 0.35)",
                "#38BDF8",
            ),
            "PROFILE_REELS": (
                "rgba(244, 63, 94, 0.15)",
                "rgba(244, 63, 94, 0.35)",
                "#FB7185",
            ),
            "REEL": ("rgba(244, 63, 94, 0.15)", "rgba(244, 63, 94, 0.35)", "#FB7185"),
            "STORY": (
                "rgba(245, 158, 11, 0.15)",
                "rgba(245, 158, 11, 0.35)",
                "#FBBF24",
            ),
            "POST": ("rgba(16, 185, 129, 0.15)", "rgba(16, 185, 129, 0.35)", "#34D399"),
            "CAROUSEL": (
                "rgba(139, 92, 246, 0.15)",
                "rgba(139, 92, 246, 0.35)",
                "#A78BFA",
            ),
            "HIGHLIGHT": (
                "rgba(236, 72, 153, 0.15)",
                "rgba(236, 72, 153, 0.35)",
                "#F472B6",
            ),
        }
        return palette.get(
            self.target_type,
            ("rgba(112, 197, 255, 0.15)", "rgba(112, 197, 255, 0.30)", "#70C5FF"),
        )

    def _init_ui(self) -> None:
        bg_col, border_col, text_col = self._get_badge_palette()

        self.setStyleSheet(
            """
            QFrame#URLItemCard {
                background-color: rgba(24, 22, 35, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 12px;
            }
            QFrame#URLItemCard:hover {
                background-color: rgba(32, 28, 48, 0.85);
                border: 1px solid rgba(225, 48, 108, 0.45);
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(12)

        # Glassmorphic Image/Icon Pod
        self.thumb_pod = URLGlassThumbnailPod(self.target_type, self)
        layout.addWidget(self.thumb_pod)

        # Media Type Badge
        self.lbl_type = QLabel(self.target_type, self)
        self.lbl_type.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_type.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_type.setFixedWidth(86)
        self.lbl_type.setFixedHeight(28)
        self.lbl_type.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg_col};
                color: {text_col};
                border: 1px solid {border_col};
                border-radius: 6px;
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 0.5px;
            }}
            """
        )
        layout.addWidget(self.lbl_type)

        display_url = (
            self.url.replace("https://www.", "")
            .replace("http://www.", "")
            .replace("https://", "")
            .replace("http://", "")
        )
        self.lbl_url = QLabel(display_url, self)
        self.lbl_url.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self.lbl_url.setStyleSheet(
            "color: #E2E8F0; background: transparent; font-size: 13px;"
        )
        self.lbl_url.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_url.setToolTip(self.url)
        layout.addWidget(self.lbl_url, stretch=1)

        self.btn_delete = QPushButton(self)
        self.btn_delete.setObjectName("CardDeleteButton")
        self.btn_delete.setFixedSize(36, 36)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setToolTip("Remove target")

        del_icon = get_icon("trash", color="#94A3B8", size=15)
        if del_icon:
            self.btn_delete.setIcon(del_icon)
            self.btn_delete.setIconSize(QSize(15, 15))

        self.btn_delete.clicked.connect(lambda: self.deleted.emit(self.url))
        layout.addWidget(self.btn_delete)


class URLChipInput(QWidget):
    urls_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._urls: List[str] = []
        self._chip_widgets: Dict[str, URLItemCard] = {}
        self._scroll_anim: Optional[QPropertyAnimation] = None

        self._init_input_bar()
        self._init_list_view()

    def _init_input_bar(self) -> None:
        self.input_widget = QWidget(self)
        layout = QHBoxLayout(self.input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.input_edit = QLineEdit(self.input_widget)
        self.input_edit.setPlaceholderText(
            "Paste Instagram links (Ctrl+V or Enter multiple links)..."
        )
        self.input_edit.setFixedHeight(42)
        self.input_edit.setStyleSheet(
            """
            QLineEdit {
                background-color: #16161F;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                padding: 8px 14px;
                color: #FFFFFF;
                font-size: 10.5pt;
            }
            QLineEdit:focus {
                border: 1.5px solid #E1306C;
                background-color: #1C1C28;
            }
            """
        )
        self.input_edit.returnPressed.connect(self._handle_manual_entry)
        self.input_edit.installEventFilter(self)
        layout.addWidget(self.input_edit, 1)

        self.btn_add = QPushButton(self.input_widget)
        self.btn_add.setObjectName("AddUrlButton")
        self.btn_add.setFixedSize(42, 42)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setToolTip("Add Instagram URL")
        add_icon = get_icon("plus", color="#FFFFFF", size=18)
        if add_icon:
            self.btn_add.setIcon(add_icon)
            self.btn_add.setIconSize(QSize(18, 18))

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
                border-radius: 10px;
                padding: 0px;
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
            }
            QPushButton#AddUrlButton:disabled {
                background: #1A1822 !important;
                border: 1px solid rgba(255, 255, 255, 0.04) !important;
            }
            """
        )
        layout.addWidget(self.btn_add)

    def _init_list_view(self) -> None:
        self.list_widget = QWidget(self)
        main_layout = QVBoxLayout(self.list_widget)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        header_bar = QHBoxLayout()
        self.lbl_list_count = QLabel("URL Links (0)", self.list_widget)
        self.lbl_list_count.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_list_count.setStyleSheet("color: #FFFFFF; font-size: 13.5px;")
        header_bar.addWidget(self.lbl_list_count)
        header_bar.addStretch()

        self.btn_clear_all = QPushButton(self.list_widget)
        self.btn_clear_all.setObjectName("DestructiveButton")
        self.btn_clear_all.setFixedSize(36, 32)
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.setToolTip("Clear All URLs")
        clear_all_icon = get_icon("trash", color="#F87171", size=15)
        if clear_all_icon:
            self.btn_clear_all.setIcon(clear_all_icon)
            self.btn_clear_all.setIconSize(QSize(15, 15))

        self.btn_clear_all.clicked.connect(self.clear)
        header_bar.addWidget(self.btn_clear_all)
        main_layout.addLayout(header_bar)

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
        self.list_layout.setSpacing(8)
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

        matches = extract_instagram_urls(text)
        tokens = matches if matches else re.split(r"[\r\n\t,;\s]+", text.strip())
        found_any = False

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if self.add_url_chip(token):
                found_any = True

        if found_any:
            self.input_edit.clear()
            self.smooth_scroll_to_bottom()
            return True
        return False

    def add_url_chip(self, url: str) -> bool:
        clean_url = url.strip()
        if not clean_url or clean_url in self._urls:
            return False

        parsed = parse_instagram_url(clean_url)
        if not parsed.get("valid"):
            return False

        target_type = str(parsed.get("type") or "MEDIA").upper()
        card = URLItemCard(
            clean_url, target_type=target_type, parent=self.list_container
        )
        card.deleted.connect(self.remove_url)

        self._urls.append(clean_url)
        self._chip_widgets[clean_url] = card

        count = self.list_layout.count()
        if count > 0:
            self.list_layout.insertWidget(count - 1, card)
        else:
            self.list_layout.addWidget(card)

        self._sync_state()
        return True

    def remove_url(self, url: str) -> None:
        if url in self._urls:
            self._urls.remove(url)
        if url in self._chip_widgets:
            widget = self._chip_widgets.pop(url)
            self.list_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._sync_state()

    def remove_url_chip(self, url: str) -> None:
        self.remove_url(url)

    def _sync_state(self) -> None:
        count = len(self._urls)
        self.lbl_list_count.setText(f"URL Links ({count})")
        self.btn_clear_all.setEnabled(count > 0)
        self.urls_changed.emit()

    def smooth_scroll_to_bottom(self) -> None:
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
