"""
gui/main_window.py - Main Window with Modern Studio Workspace Layout.
Optimized for spacious media grid inspection and streamlined controls.
"""

import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PyQt6.QtCore import (
    QEasingCurve,
    QLocale,
    QPropertyAnimation,
    QSettings,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config.constants import APPLICATION_NAME, ORGANIZATION_NAME
from config.translations import TRANSLATIONS
from core.cookie_manager import sanitize_and_save_instagram_cookies
from core.download_worker import GridDownloadWorker
from core.inspect_worker import InspectionWorker
from gui.icons import get_icon
from gui.styles import DARK_THEME_QSS
from gui.widgets.media_card import MediaCardWidget
from gui.widgets.modern_progress_bar import ModernProgressBar
from gui.widgets.url_chip_input import UrlBlockContainer
from utils.file_utils import get_icon_path


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(880, 680)
        self.resize(960, 780)

        icon_file = get_icon_path()
        if os.path.exists(icon_file):
            self.setWindowIcon(QIcon(icon_file))

        self.settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
        self.inspect_worker: InspectionWorker | None = None
        self.download_worker: GridDownloadWorker | None = None
        self.cards: list[MediaCardWidget] = []
        self.anchor_card_idx: int | None = None
        self.last_clipboard_text = ""

        system_lang = "th" if QLocale.system().name().startswith("th") else "en"
        self.current_lang = str(self.settings.value("language", system_lang))

        self.load_paths()
        self.init_ui()
        self.apply_dark_theme()
        self.setup_clipboard_monitor()
        self.load_saved_settings()
        self.retranslate_ui()

    def t(self, key: str) -> str:
        return TRANSLATIONS.get(self.current_lang, {}).get(key, key)

    def load_paths(self) -> None:
        default_dl = os.path.join(
            os.path.expanduser("~"), "Downloads", "InstagramDownloads"
        )
        self.save_dir = str(self.settings.value("save_dir", default_dl))
        app_data_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), APPLICATION_NAME
        )
        os.makedirs(app_data_dir, exist_ok=True)
        self.cookie_path = os.path.join(app_data_dir, "cookies.txt")

    def init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)

        # 1. TOP HEADER & URL INPUT DECK
        self.input_group = QGroupBox()
        input_layout = QVBoxLayout(self.input_group)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(6)

        header_top_row = QHBoxLayout()
        self.title_label = QLabel("Instagram Pro Downloader - Studio Inspector")
        self.title_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header_top_row.addWidget(self.title_label)

        header_top_row.addStretch()

        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem("🇹🇭 ภาษาไทย", "th")
        self.cmb_lang.addItem("🇺🇸 English", "en")
        self.cmb_lang.setFixedHeight(24)
        idx = self.cmb_lang.findData(self.current_lang)
        if idx >= 0:
            self.cmb_lang.setCurrentIndex(idx)
        self.cmb_lang.currentIndexChanged.connect(self.on_language_changed)
        header_top_row.addWidget(self.cmb_lang)
        input_layout.addLayout(header_top_row)

        self.url_container = UrlBlockContainer(self.current_lang)
        input_layout.addWidget(self.url_container)

        input_action_row = QHBoxLayout()
        self.chk_clipboard = QCheckBox()
        input_action_row.addWidget(self.chk_clipboard, stretch=1)

        self.btn_inspect = QPushButton()
        self.btn_inspect.setFixedHeight(28)
        self.btn_inspect.setIcon(get_icon("search", "#ffffff", 14))
        self.btn_inspect.setStyleSheet(
            "background-color: #007acc; font-size: 11px; padding: 0 16px;"
        )
        self.btn_inspect.clicked.connect(self.start_inspection)
        input_action_row.addWidget(self.btn_inspect)

        self.btn_clear_input = QPushButton()
        self.btn_clear_input.setFixedHeight(28)
        self.btn_clear_input.setIcon(get_icon("clear", "#ffffff", 13))
        self.btn_clear_input.setStyleSheet(
            "background-color: #3d3d4e; font-size: 11px; padding: 0 12px;"
        )
        self.btn_clear_input.clicked.connect(lambda: self.url_container.clear())
        input_action_row.addWidget(self.btn_clear_input)

        input_layout.addLayout(input_action_row)
        main_layout.addWidget(self.input_group)

        # 2. MAIN MEDIA GRID INSPECTOR
        self.grid_group = QGroupBox()
        grid_layout = QVBoxLayout(self.grid_group)
        grid_layout.setContentsMargins(10, 8, 10, 8)
        grid_layout.setSpacing(6)

        sel_toolbar = QHBoxLayout()
        self.btn_select_all = QPushButton()
        self.btn_select_all.setFixedHeight(24)
        self.btn_select_all.setIcon(get_icon("check-all", "#ffffff", 13))
        self.btn_select_all.setStyleSheet(
            "background-color: #2e2e3d; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_select_all.clicked.connect(self.select_all_cards)
        sel_toolbar.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton()
        self.btn_delete_selected.setFixedHeight(24)
        self.btn_delete_selected.setIcon(get_icon("trash", "#ffffff", 13))
        self.btn_delete_selected.setStyleSheet(
            "background-color: #8b2635; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        sel_toolbar.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton()
        self.btn_clear_completed.setFixedHeight(24)
        self.btn_clear_completed.setIcon(get_icon("clear-completed", "#ffffff", 13))
        self.btn_clear_completed.setStyleSheet(
            "background-color: #3b3b4f; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        sel_toolbar.addWidget(self.btn_clear_completed)

        sel_toolbar.addStretch()
        self.lbl_selection_count = QLabel()
        self.lbl_selection_count.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        sel_toolbar.addWidget(self.lbl_selection_count)
        grid_layout.addLayout(sel_toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("mediaGridScroll")
        self.scroll_area.setStyleSheet(
            """
            #mediaGridScroll {
                background-color: #17171e;
                border: 1px solid #252533;
                border-radius: 6px;
            }
        """
        )

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(8)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.cards_container)
        grid_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.grid_group, stretch=1)

        # 3. BOTTOM COMMAND & SETTINGS DECK
        self.bottom_deck = QFrame()
        self.bottom_deck.setStyleSheet(
            """
            QFrame#bottomDeck {
                background-color: #1c1c24;
                border: 1px solid #2e2e3d;
                border-radius: 6px;
            }
        """
        )
        self.bottom_deck.setObjectName("bottomDeck")
        bottom_deck_layout = QVBoxLayout(self.bottom_deck)
        bottom_deck_layout.setContentsMargins(10, 8, 10, 8)
        bottom_deck_layout.setSpacing(6)

        deck_row = QHBoxLayout()
        deck_row.setSpacing(12)

        left_settings_col = QVBoxLayout()
        left_settings_col.setSpacing(4)

        path_row = QHBoxLayout()
        self.lbl_path = QLabel()
        self.lbl_path.setStyleSheet("color: #cccccc; font-size: 11px;")
        path_row.addWidget(self.lbl_path, stretch=1)

        self.btn_browse = QPushButton()
        self.btn_browse.setFixedHeight(24)
        self.btn_browse.setIcon(get_icon("folder", "#ffffff", 12))
        self.btn_browse.setStyleSheet(
            "background-color: #2c2c3d; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_browse.clicked.connect(self.browse_folder)
        path_row.addWidget(self.btn_browse)

        self.btn_open = QPushButton()
        self.btn_open.setFixedHeight(24)
        self.btn_open.setIcon(get_icon("folder-open", "#ffffff", 12))
        self.btn_open.setStyleSheet(
            "background-color: #2c2c3d; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_open.clicked.connect(self.open_folder)
        path_row.addWidget(self.btn_open)
        left_settings_col.addLayout(path_row)

        cookie_row = QHBoxLayout()
        self.lbl_cookie_status = QLabel()
        self.lbl_cookie_status.setStyleSheet("font-size: 11px;")
        cookie_row.addWidget(self.lbl_cookie_status, stretch=1)

        self.btn_import_cookie = QPushButton()
        self.btn_import_cookie.setFixedHeight(24)
        self.btn_import_cookie.setIcon(get_icon("cookie", "#ffffff", 12))
        self.btn_import_cookie.setStyleSheet(
            "background-color: #2c2c3d; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_import_cookie.clicked.connect(self.import_cookie_file)
        cookie_row.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton()
        self.btn_clear_cookie.setFixedHeight(24)
        self.btn_clear_cookie.setIcon(get_icon("trash", "#ffffff", 12))
        self.btn_clear_cookie.setStyleSheet(
            "background-color: #8b2635; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_clear_cookie.clicked.connect(self.clear_cookie_file)
        cookie_row.addWidget(self.btn_clear_cookie)
        left_settings_col.addLayout(cookie_row)

        deck_row.addLayout(left_settings_col, stretch=3)

        right_action_col = QHBoxLayout()
        right_action_col.setSpacing(6)

        self.btn_download_all = QPushButton()
        self.btn_download_all.setFixedHeight(48)
        self.btn_download_all.setIcon(get_icon("download", "#ffffff", 18))
        self.btn_download_all.setStyleSheet(
            """
            QPushButton {
                background-color: #d62976;
                font-size: 13px;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #fa7e1e; }
            QPushButton:disabled { background-color: #2c2c38; color: #606070; }
        """
        )
        self.btn_download_all.clicked.connect(self.start_download_all)
        right_action_col.addWidget(self.btn_download_all, stretch=3)

        self.btn_cancel = QPushButton()
        self.btn_cancel.setFixedHeight(48)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setIcon(get_icon("stop", "#ffffff", 15))
        self.btn_cancel.setStyleSheet(
            """
            QPushButton {
                background-color: #4a4a5a;
                font-size: 11px;
                font-weight: bold;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover { background-color: #8b2635; }
            QPushButton:disabled { background-color: #22222b; color: #555566; }
        """
        )
        self.btn_cancel.clicked.connect(self.cancel_operation)
        right_action_col.addWidget(self.btn_cancel, stretch=1)

        deck_row.addLayout(right_action_col, stretch=2)
        bottom_deck_layout.addLayout(deck_row)

        status_bar_row = QHBoxLayout()
        self.lbl_status = QLabel()
        self.lbl_status.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        status_bar_row.addWidget(self.lbl_status)

        status_bar_row.addStretch()

        self.lbl_footer = QLabel()
        self.lbl_footer.setStyleSheet("color: #888888; font-size: 10px;")
        status_bar_row.addWidget(self.lbl_footer)
        bottom_deck_layout.addLayout(status_bar_row)

        self.progress_bar = ModernProgressBar()
        bottom_deck_layout.addWidget(self.progress_bar)

        main_layout.addWidget(self.bottom_deck)

    def keyPressEvent(self, event) -> None:
        is_input_focused = self.url_container.txt_input.hasFocus()

        if event.matches(QKeySequence.StandardKey.SelectAll) or (
            event.key() == Qt.Key.Key_A
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if not is_input_focused:
                self.select_all_cards()
                return

        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not is_input_focused:
                self.delete_selected_cards()
                return

        elif event.key() == Qt.Key.Key_Escape:
            self.deselect_all_cards()
            return

        super().keyPressEvent(event)

    def on_card_clicked(self, card: MediaCardWidget, event) -> None:
        modifiers = event.modifiers()
        idx = self.cards.index(card)

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            card.set_selected(not card.is_selected)
            self.anchor_card_idx = idx

        elif (
            modifiers & Qt.KeyboardModifier.ShiftModifier
            and self.anchor_card_idx is not None
        ):
            start = min(self.anchor_card_idx, idx)
            end = max(self.anchor_card_idx, idx)
            for i, c in enumerate(self.cards):
                c.set_selected(start <= i <= end)

        else:
            for c in self.cards:
                c.set_selected(c == card)
            self.anchor_card_idx = idx

        self.update_selection_ui()

    def select_all_cards(self) -> None:
        for card in self.cards:
            card.set_selected(True)
        self.update_selection_ui()

    def deselect_all_cards(self) -> None:
        for card in self.cards:
            card.set_selected(False)
        self.anchor_card_idx = None
        self.update_selection_ui()

    def delete_selected_cards(self) -> None:
        selected_cards = [c for c in self.cards if c.is_selected]
        for card in selected_cards:
            self.remove_card(card)
        self.update_selection_ui()

    def clear_completed_cards(self) -> None:
        completed_cards = [c for c in self.cards if c.is_completed]
        for card in completed_cards:
            self.remove_card(card)
        self.update_selection_ui()

    def update_selection_ui(self) -> None:
        selected_count = sum(1 for c in self.cards if c.is_selected)
        total_count = len(self.cards)
        self.lbl_selection_count.setText(
            self.t("lbl_selection_format").format(
                selected=selected_count, total=total_count
            )
        )
        # Disable Download button if the grid is completely empty
        self.btn_download_all.setEnabled(total_count > 0)

    def update_cookie_status_ui(self) -> None:
        has_cookie = os.path.exists(self.cookie_path)
        if has_cookie:
            self.lbl_cookie_status.setText(self.t("cookie_connected"))
            self.lbl_cookie_status.setStyleSheet("color: #28a745; font-size: 11px;")
            self.btn_clear_cookie.setEnabled(True)
        else:
            self.lbl_cookie_status.setText(self.t("cookie_none"))
            self.lbl_cookie_status.setStyleSheet("color: #888888; font-size: 11px;")
            self.btn_clear_cookie.setEnabled(False)

    def import_cookie_file(self) -> None:
        reply = QMessageBox.warning(
            self,
            self.t("cookie_warn_title"),
            self.t("cookie_warn_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select cookies.txt",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if file_path:
            success, msg = sanitize_and_save_instagram_cookies(
                file_path, self.cookie_path, self.current_lang
            )
            self.update_cookie_status_ui()
            if success:
                QMessageBox.information(self, self.t("success_title"), msg)
            else:
                QMessageBox.critical(self, "Error", msg)

    def clear_cookie_file(self) -> None:
        if os.path.exists(self.cookie_path):
            reply = QMessageBox.question(
                self,
                self.t("cookie_clear_title"),
                self.t("cookie_clear_msg"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    os.remove(self.cookie_path)
                    self.update_cookie_status_ui()
                    QMessageBox.information(
                        self, "Success", "Cookie deleted successfully."
                    )
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Could not remove file: {e}")

    def on_language_changed(self) -> None:
        self.current_lang = self.cmb_lang.currentData()
        self.settings.setValue("language", self.current_lang)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(self.t("title"))
        self.title_label.setText(self.t("title"))
        self.input_group.setTitle(self.t("url_group"))
        self.chk_clipboard.setText(self.t("clipboard_chk"))
        self.chk_clipboard.setToolTip(self.t("clipboard_tooltip"))
        self.btn_inspect.setText(self.t("btn_inspect"))
        self.btn_clear_input.setText(self.t("btn_clear_input"))
        self.grid_group.setTitle(self.t("grid_group"))
        self.btn_select_all.setText(self.t("btn_select_all"))
        self.btn_delete_selected.setText(self.t("btn_delete_selected"))
        self.btn_clear_completed.setText(self.t("btn_clear_completed"))
        self.lbl_path.setText(f"{self.t('save_path_prefix')}{self.save_dir}")
        self.btn_browse.setText(self.t("btn_browse"))
        self.btn_open.setText(self.t("btn_open"))
        self.btn_import_cookie.setText(self.t("btn_import_cookie"))
        self.btn_clear_cookie.setText(self.t("btn_clear_cookie"))
        self.btn_download_all.setText(self.t("btn_download_all"))
        self.btn_cancel.setText(self.t("btn_cancel"))
        self.lbl_status.setText(self.t("status_ready"))
        self.update_selection_ui()
        self.update_cookie_status_ui()
        self.url_container.retranslate_ui(self.current_lang)

        for card in self.cards:
            card.retranslate_ui(self.current_lang)

    def apply_dark_theme(self) -> None:
        self.setStyleSheet(DARK_THEME_QSS)

    def load_saved_settings(self) -> None:
        self.chk_clipboard.setChecked(
            self.settings.value("auto_clipboard", True, type=bool)
        )

    def setup_clipboard_monitor(self) -> None:
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_change)

    def on_clipboard_change(self) -> None:
        if not self.chk_clipboard.isChecked():
            return

        text = self.clipboard.text().strip()
        if not text or text == self.last_clipboard_text:
            return

        added = self.url_container.add_from_text(text)
        if added > 0:
            self.last_clipboard_text = text
            self.lbl_footer.setText(self.t("footer_clip_detected"))
            self.lbl_footer.setStyleSheet("color: #28a745; font-size: 10px;")

    def start_inspection(self) -> None:
        targets = self.url_container.get_targets()
        if not targets:
            QMessageBox.warning(self, "Warning", self.t("warn_no_url"))
            return

        has_story = any(
            t["type"] in ("story", "highlight", "story_user") for t in targets
        )

        if has_story and not os.path.exists(self.cookie_path):
            QMessageBox.warning(
                self, "Cookie Required", self.t("warn_story_need_cookie")
            )
            return

        self.btn_inspect.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText(self.t("status_inspecting"))

        existing_shortcodes = {
            card.data.get("shortcode")
            for card in self.cards
            if card.data.get("shortcode")
        }

        self.inspect_worker = InspectionWorker(
            targets, self.cookie_path, existing_shortcodes=existing_shortcodes
        )
        self.inspect_worker.item_inspected.connect(self.add_card)
        self.inspect_worker.progress_status.connect(
            lambda msg: self.lbl_status.setText(msg)
        )
        self.inspect_worker.finished_inspection.connect(self.on_inspection_finished)
        self.inspect_worker.start()

    def add_card(self, data: dict) -> None:
        shortcode = data.get("shortcode")

        for existing_card in self.cards:
            if existing_card.data.get("shortcode") == shortcode:
                return

        card = MediaCardWidget(data, self.current_lang)
        card.clicked.connect(self.on_card_clicked)
        card.removed.connect(self.remove_card)

        self.cards.append(card)
        self.cards_layout.addWidget(card)
        card.animate_entry()

        self.update_selection_ui()
        self.scroll_grid_to_bottom()

    def scroll_grid_to_bottom(self) -> None:
        QTimer.singleShot(30, self._do_scroll_grid_to_bottom)

    def _do_scroll_grid_to_bottom(self) -> None:
        scroll_bar = self.scroll_area.verticalScrollBar()
        self.grid_scroll_anim = QPropertyAnimation(scroll_bar, b"value", self)
        self.grid_scroll_anim.setDuration(240)
        self.grid_scroll_anim.setStartValue(scroll_bar.value())
        self.grid_scroll_anim.setEndValue(scroll_bar.maximum())
        self.grid_scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.grid_scroll_anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def remove_card(self, card: MediaCardWidget) -> None:
        if card in self.cards:
            if card.thumb_loader and card.thumb_loader.isRunning():
                card.thumb_loader.cancel()
                try:
                    card.thumb_loader.loaded.disconnect()
                except Exception:
                    pass
                card.thumb_loader.wait(300)

            self.cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
            self.anchor_card_idx = None
            self.update_selection_ui()

    def on_inspection_finished(self, count: int) -> None:
        self.btn_inspect.setEnabled(True)
        self.btn_download_all.setEnabled(len(self.cards) > 0)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText(self.t("inspect_done").format(count=len(self.cards)))
        self.url_container.clear()
        self.update_selection_ui()

    def start_download_all(self) -> None:
        # 1. Check if the grid has any items at all
        if not self.cards:
            QMessageBox.warning(self, "Warning", "No media items in queue to download.")
            return

        # 2. Determine target cards (selected cards or all cards in queue)
        selected_cards = [c for c in self.cards if c.is_selected]
        targets = selected_cards if selected_cards else self.cards

        # 3. Filter only cards that are NOT completed yet
        target_cards = [c for c in targets if not c.is_completed]

        if not target_cards:
            QMessageBox.information(
                self, "Info", "All items in the queue are already downloaded."
            )
            return

        self.btn_download_all.setEnabled(False)
        self.btn_inspect.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0.0)

        self.download_worker = GridDownloadWorker(
            target_cards, self.save_dir, self.cookie_path
        )
        self.download_worker.item_started.connect(self.on_item_download_started)
        self.download_worker.item_finished.connect(self.on_item_download_finished)
        self.download_worker.progress_signal.connect(self.update_progress)
        self.download_worker.all_finished.connect(self.on_all_downloads_finished)
        self.download_worker.start()

    def on_item_download_started(
        self, card_idx: int, total: int, shortcode: str
    ) -> None:
        if self.download_worker and card_idx < len(self.download_worker.target_cards):
            card = self.download_worker.target_cards[card_idx]
            card.lbl_status.setText("Downloading...")
            card.lbl_status.setStyleSheet("color: #fa7e1e; font-size: 11px;")
        self.lbl_status.setText(f"Downloading [{card_idx + 1}/{total}]: {shortcode}")

    def on_item_download_finished(
        self, card_idx: int, ok: bool, text: str, saved_path: str
    ) -> None:
        if self.download_worker and card_idx < len(self.download_worker.target_cards):
            card = self.download_worker.target_cards[card_idx]
            if ok:
                card.mark_completed(saved_path)
            else:
                card.lbl_status.setText("✖ Failed")
                card.lbl_status.setStyleSheet("color: #dc3545; font-size: 11px;")

    def update_progress(self, d: dict) -> None:
        self.progress_bar.setValue(int(d["percent"]))
        dl_mb = d["downloaded"] / (1024 * 1024)
        total_mb = d["total"] / (1024 * 1024) if d["total"] else 0
        speed = d["speed"]
        speed_str = (
            f"{speed / (1024 * 1024):.2f} MB/s"
            if speed > 1024 * 1024
            else f"{speed / 1024:.1f} KB/s"
        )
        if total_mb > 0:
            self.lbl_status.setText(
                f"Downloading... {dl_mb:.2f} / {total_mb:.2f} MB ({speed_str})"
            )

    def on_all_downloads_finished(
        self, success: int, fail: int, is_cancelled: bool
    ) -> None:
        self.btn_inspect.setEnabled(True)
        self.btn_download_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(100 if not is_cancelled else 0)

        if is_cancelled:
            self.lbl_status.setText(self.t("status_cancelled"))
            return

        self.lbl_status.setText(f"Finished: {success} Success | {fail} Failed")
        QMessageBox.information(
            self,
            self.t("success_title"),
            self.t("success_msg").format(success=success, path=self.save_dir),
        )

    def cancel_operation(self) -> None:
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
        self.lbl_status.setText(self.t("status_cancelled"))
        self.btn_cancel.setEnabled(False)

    def browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Folder", self.save_dir)
        if chosen:
            self.save_dir = chosen
            self.lbl_path.setText(f"{self.t('save_path_prefix')}{self.save_dir}")

    def open_folder(self) -> None:
        os.makedirs(self.save_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(os.path.normpath(self.save_dir))
        else:
            subprocess.Popen(["xdg-open", self.save_dir])

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            try:
                self.inspect_worker.item_inspected.disconnect()
                self.inspect_worker.finished_inspection.disconnect()
                self.inspect_worker.progress_status.disconnect()
            except Exception:
                pass
            self.inspect_worker.wait(1000)

        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            try:
                self.download_worker.item_started.disconnect()
                self.download_worker.item_finished.disconnect()
                self.download_worker.progress_signal.disconnect()
                self.download_worker.all_finished.disconnect()
            except Exception:
                pass
            self.download_worker.wait(1000)

        for card in self.cards:
            if card.thumb_loader and card.thumb_loader.isRunning():
                card.thumb_loader.cancel()
                try:
                    card.thumb_loader.loaded.disconnect()
                except Exception:
                    pass
                card.thumb_loader.wait(300)

        self.settings.setValue("save_dir", self.save_dir)
        self.settings.setValue("auto_clipboard", self.chk_clipboard.isChecked())
        self.settings.setValue("language", self.current_lang)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
