"""
gui/widgets/url_chip_input.py - Interactive URL Block & Chip Management Widget.
Features Profile Scope Selector (All / Videos Only / Photos Only).
"""

import re
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.constants import INSTAGRAM_URL_REGEX
from config.translations import TRANSLATIONS
from core.parser import parse_instagram_url
from gui.icons import get_icon


class UrlBlockItem(QFrame):
    """บล็อกแสดงผลของแต่ละ URL พร้อม Scope Selector สำหรับ Profile"""

    removed = pyqtSignal(object)

    def __init__(self, raw_url: str, lang: str = "th"):
        super().__init__()
        self.raw_url = raw_url
        self.lang = lang
        self.parsed = parse_instagram_url(raw_url)
        self.init_ui()

    def init_ui(self) -> None:
        self.setFixedHeight(32)
        self.setStyleSheet(
            """
            UrlBlockItem {
                background-color: #1f1f2a;
                border: 1px solid #333346;
                border-radius: 5px;
            }
            UrlBlockItem:hover {
                border: 1px solid #5a5a75;
                background-color: #262635;
            }
        """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 6, 2)
        layout.setSpacing(8)

        # 1. Type Badge & Icon
        m_type = self.parsed.get("type", "post") if self.parsed else "post"
        if m_type in ("story", "highlight", "story_user"):
            badge_text = "STORY"
            badge_color = "#9b51e0"
            icon_name = "story"
        elif m_type == "profile_reels":
            badge_text = "ALL REELS"
            badge_color = "#fa7e1e"
            icon_name = "video"
        elif m_type == "profile_posts":
            badge_text = "PROFILE"
            badge_color = "#e1306c"
            icon_name = "folder"
        elif m_type == "video":
            badge_text = "REEL"
            badge_color = "#d62976"
            icon_name = "video"
        else:
            badge_text = "POST"
            badge_color = "#4a90e2"
            icon_name = "photo"

        lbl_icon = QLabel()
        lbl_icon.setPixmap(get_icon(icon_name, badge_color, 13).pixmap(13, 13))
        layout.addWidget(lbl_icon)

        lbl_badge = QLabel(badge_text)
        lbl_badge.setStyleSheet(
            f"""
            background-color: {badge_color};
            color: #ffffff;
            font-size: 9px;
            font-weight: bold;
            border-radius: 3px;
            padding: 1px 4px;
        """
        )
        layout.addWidget(lbl_badge)

        # 2. URL Text
        clean_url = self.parsed["clean_url"] if self.parsed else self.raw_url
        self.lbl_url = QLabel(clean_url)
        self.lbl_url.setStyleSheet("color: #eaeaea; font-size: 11px;")
        self.lbl_url.setToolTip(clean_url)
        layout.addWidget(self.lbl_url, stretch=1)

        # 3. Scope Selector Dropdown (สำหรับ Profile URLs)
        if m_type in ("profile_posts", "profile_reels"):
            self.cmb_scope = QComboBox()
            self.cmb_scope.setFixedHeight(22)
            self.cmb_scope.setStyleSheet(
                """
                QComboBox {
                    background-color: #2a2a3b;
                    color: #e0e0ff;
                    border: 1px solid #4a4a65;
                    border-radius: 4px;
                    padding: 1px 6px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QComboBox:hover {
                    border: 1px solid #fa7e1e;
                }
                QComboBox QAbstractItemView {
                    background-color: #20202d;
                    color: #ffffff;
                    selection-background-color: #d62976;
                }
            """
            )
            self.populate_scope_options()
            if m_type == "profile_reels":
                self.cmb_scope.setCurrentIndex(1)  # Default: เฉพาะ Reels
            layout.addWidget(self.cmb_scope)

        # 4. Delete Button
        btn_del = QPushButton()
        btn_del.setFixedSize(18, 18)
        btn_del.setIcon(get_icon("clear", "#ff6b81", 10))
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 9px;
            }
            QPushButton:hover {
                background-color: #8b2635;
            }
        """
        )
        btn_del.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(btn_del)

    def populate_scope_options(self) -> None:
        if hasattr(self, "cmb_scope"):
            cur_data = self.cmb_scope.currentData()
            self.cmb_scope.clear()
            self.cmb_scope.addItem(TRANSLATIONS[self.lang]["scope_all"], "all")
            self.cmb_scope.addItem(
                TRANSLATIONS[self.lang]["scope_videos_only"], "videos_only"
            )
            self.cmb_scope.addItem(
                TRANSLATIONS[self.lang]["scope_photos_only"], "photos_only"
            )
            if cur_data:
                idx = self.cmb_scope.findData(cur_data)
                if idx >= 0:
                    self.cmb_scope.setCurrentIndex(idx)

    def get_target_data(self) -> dict:
        scope = self.cmb_scope.currentData() if hasattr(self, "cmb_scope") else "all"
        return {
            "url": self.parsed["clean_url"] if self.parsed else self.raw_url,
            "type": self.parsed.get("type", "post") if self.parsed else "post",
            "scope": scope,
        }

    def get_url(self) -> str:
        return self.parsed["clean_url"] if self.parsed else self.raw_url

    def animate_entry(self, duration: int = 240) -> None:
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setDuration(duration)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(lambda: self.setGraphicsEffect(None))
        self.anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class UrlBlockContainer(QWidget):
    """Container จัดการรายการ URL ทั้งหมด"""

    urls_changed = pyqtSignal(int)

    def __init__(self, lang: str = "th"):
        super().__init__()
        self.lang = lang
        self.blocks: list[UrlBlockItem] = []
        self.init_ui()

    def init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self.txt_input = QLineEdit()
        self.txt_input.setFixedHeight(30)
        self.txt_input.setPlaceholderText(TRANSLATIONS[self.lang]["url_placeholder"])
        self.txt_input.setStyleSheet(
            """
            QLineEdit {
                background-color: #20202a;
                border: 1px solid #38384a;
                border-radius: 5px;
                padding: 4px 10px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #fa7e1e;
            }
        """
        )
        self.txt_input.returnPressed.connect(self._on_submit_input)
        input_bar.addWidget(self.txt_input, stretch=1)

        self.btn_add = QPushButton("เพิ่ม" if self.lang == "th" else "Add")
        self.btn_add.setFixedHeight(30)
        self.btn_add.setIcon(get_icon("search", "#ffffff", 12))
        self.btn_add.setStyleSheet(
            """
            QPushButton {
                background-color: #2c2c3d;
                border: 1px solid #4a4a62;
                border-radius: 5px;
                font-size: 11px;
                padding: 4px 14px;
            }
            QPushButton:hover {
                background-color: #3b3b52;
                border: 1px solid #fa7e1e;
            }
        """
        )
        self.btn_add.clicked.connect(self._on_submit_input)
        input_bar.addWidget(self.btn_add)
        main_layout.addLayout(input_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedHeight(85)
        self.scroll.setStyleSheet(
            """
            QScrollArea {
                background-color: #17171e;
                border: 1px solid #282836;
                border-radius: 5px;
            }
        """
        )

        self.blocks_widget = QWidget()
        self.blocks_layout = QVBoxLayout(self.blocks_widget)
        self.blocks_layout.setContentsMargins(6, 6, 6, 6)
        self.blocks_layout.setSpacing(4)
        self.blocks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.lbl_empty = QLabel("ยังไม่มีลิงก์ในคิว (พิมพ์ วาง URL หรือคัดลอกลิงก์ IG)")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet(
            "color: #606075; font-size: 11px; padding: 20px 0;"
        )
        self.blocks_layout.addWidget(self.lbl_empty)

        self.scroll.setWidget(self.blocks_widget)
        main_layout.addWidget(self.scroll)

    def _on_submit_input(self) -> None:
        raw_text = self.txt_input.text().strip()
        if not raw_text:
            return
        self.add_from_text(raw_text)
        self.txt_input.clear()

    def add_from_text(self, text: str) -> int:
        matches = re.findall(INSTAGRAM_URL_REGEX, text)
        clean_urls = []
        for m in matches:
            u = m.rstrip(".,;)]}>\"'?")
            clean_urls.append(u)

        if not clean_urls and "instagram.com" in text:
            clean_urls = [text.strip()]

        return self.add_urls(clean_urls)

    def add_urls(self, urls: list[str]) -> int:
        existing_urls = {b.get_url() for b in self.blocks}
        added_count = 0

        for url in urls:
            parsed = parse_instagram_url(url)
            clean_url = parsed["clean_url"] if parsed else url
            if clean_url not in existing_urls:
                block = UrlBlockItem(clean_url, self.lang)
                block.removed.connect(self.remove_block)
                self.blocks.append(block)
                self.blocks_layout.addWidget(block)
                block.animate_entry()
                existing_urls.add(clean_url)
                added_count += 1

        self.lbl_empty.setVisible(len(self.blocks) == 0)
        if added_count > 0:
            self.urls_changed.emit(len(self.blocks))
            self.scroll_to_bottom()
        return added_count

    def scroll_to_bottom(self) -> None:
        QTimer.singleShot(30, self._do_scroll_to_bottom)

    def _do_scroll_to_bottom(self) -> None:
        scroll_bar = self.scroll.verticalScrollBar()
        self.scroll_anim = QPropertyAnimation(scroll_bar, b"value", self)
        self.scroll_anim.setDuration(220)
        self.scroll_anim.setStartValue(scroll_bar.value())
        self.scroll_anim.setEndValue(scroll_bar.maximum())
        self.scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def remove_block(self, block: UrlBlockItem) -> None:
        if block in self.blocks:
            self.blocks.remove(block)
            self.blocks_layout.removeWidget(block)
            block.deleteLater()
            self.lbl_empty.setVisible(len(self.blocks) == 0)
            self.urls_changed.emit(len(self.blocks))

    def get_targets(self) -> list[dict]:
        """คืนค่าลิสต์ของเป้าหมายพร้อมประเภทและ Scope การดาวน์โหลด"""
        return [b.get_target_data() for b in self.blocks]

    def get_urls(self) -> list[str]:
        return [b.get_url() for b in self.blocks]

    def clear(self) -> None:
        for block in self.blocks:
            self.blocks_layout.removeWidget(block)
            block.deleteLater()
        self.blocks.clear()
        self.lbl_empty.setVisible(True)
        self.urls_changed.emit(0)

    def count(self) -> int:
        return len(self.blocks)

    def retranslate_ui(self, lang: str) -> None:
        self.lang = lang
        self.txt_input.setPlaceholderText(TRANSLATIONS[self.lang]["url_placeholder"])
        self.btn_add.setText("เพิ่ม" if lang == "th" else "Add")
        self.lbl_empty.setText(
            "ยังไม่มีลิงก์ในคิว (พิมพ์ วาง URL หรือคัดลอกลิงก์ IG)"
            if lang == "th"
            else "No URLs in queue (Type, paste, or copy IG links)"
        )
        for b in self.blocks:
            b.lang = lang
            b.populate_scope_options()
