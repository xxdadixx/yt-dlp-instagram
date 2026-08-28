"""
gui/main_window.py - Main GUI Window coordinating input, workers, progress, and media cards.
"""

import os
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QClipboard, QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from config.constants import DEFAULT_DOWNLOAD_DIR, DEFAULT_SESSION_DIR
from config.translations import TRANSLATIONS
from core.cookie_manager import (
    CookieManager,
    get_cookie_opener,
    sanitize_and_save_instagram_cookies,
)
from core.download_worker import DownloadWorker
from core.inspect_worker import InspectWorker
from core.parser import parse_instagram_input
from gui.styles import MAIN_STYLESHEET
from gui.widgets.media_card import MediaCard
from gui.widgets.modern_progress_bar import ModernProgressBar
from gui.widgets.no_scroll_combo import NoScrollComboBox
from gui.widgets.url_chip_input import UrlChipInput
from utils.logger import get_logger

logger = get_logger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_lang = "en"
        self.cookie_file_path = os.path.join(DEFAULT_SESSION_DIR, "cookies.txt")
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        self.media_cards: list[MediaCard] = []

        self.inspect_worker: InspectWorker | None = None
        self.download_worker: DownloadWorker | None = None

        self.init_ui()
        self.update_cookie_status_ui()
        self.retranslate_ui()

    def init_ui(self):
        self.setWindowTitle("Instagram Pro Downloader - Studio Inspector")
        self.resize(1100, 750)
        self.setMinimumSize(850, 600)
        self.setStyleSheet(MAIN_STYLESHEET)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ----------------------------------------------------------------------
        # Top Header Bar: Title + Language Switcher
        # ----------------------------------------------------------------------
        header_layout = QHBoxLayout()
        self.lbl_header = QLabel("Instagram Pro Downloader - Studio Inspector")
        self.lbl_header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #ff7537;"
        )
        header_layout.addWidget(self.lbl_header)

        header_layout.addStretch()

        self.combo_lang = NoScrollComboBox()
        self.combo_lang.addItem("🇺🇸 English", "en")
        self.combo_lang.addItem("🇹🇭 ภาษาไทย", "th")
        self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        header_layout.addWidget(self.combo_lang)
        main_layout.addLayout(header_layout)

        # ----------------------------------------------------------------------
        # URL Input Section
        # ----------------------------------------------------------------------
        self.url_container = UrlChipInput(self)
        main_layout.addWidget(self.url_container)

        # Action Buttons below input
        input_actions_layout = QHBoxLayout()
        self.btn_inspect = QPushButton("🔍 Inspect Media")
        self.btn_inspect.setStyleSheet(
            "background-color: #0084ff; font-weight: bold; padding: 8px 16px;"
        )
        self.btn_inspect.clicked.connect(self.start_inspection)
        input_actions_layout.addWidget(self.btn_inspect)

        self.btn_clear_text = QPushButton("✕ Clear Textbox")
        self.btn_clear_text.clicked.connect(self.url_container.clear_input)
        input_actions_layout.addWidget(self.btn_clear_text)

        input_actions_layout.addStretch()
        main_layout.addLayout(input_actions_layout)

        # ----------------------------------------------------------------------
        # Media Queue Section (Toolbar + Scrollable Grid)
        # ----------------------------------------------------------------------
        queue_toolbar_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("☑ Select All (Ctrl+A)")
        self.btn_select_all.clicked.connect(self.on_select_all)
        queue_toolbar_layout.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton("🗑 Delete Selected (Del)")
        self.btn_delete_selected.clicked.connect(self.on_delete_selected)
        queue_toolbar_layout.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton("🧹 Clear Completed")
        self.btn_clear_completed.clicked.connect(self.on_clear_completed)
        queue_toolbar_layout.addWidget(self.btn_clear_completed)

        queue_toolbar_layout.addStretch()
        self.lbl_selected_counter = QLabel("Selected: 0 / 0 items")
        self.lbl_selected_counter.setStyleSheet("color: #aaaaaa; font-weight: bold;")
        queue_toolbar_layout.addWidget(self.lbl_selected_counter)
        main_layout.addLayout(queue_toolbar_layout)

        # Scroll Area for Media Cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(4, 4, 4, 4)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)
        main_layout.addWidget(self.scroll_area, stretch=1)

        # ----------------------------------------------------------------------
        # Bottom Controls: Save Folder, Cookies, Download Actions & Progress
        # ----------------------------------------------------------------------
        bottom_layout = QVBoxLayout()
        bottom_layout.setSpacing(8)

        folder_cookie_layout = QHBoxLayout()
        self.lbl_save_folder = QLabel(f"Save Folder: {self.download_dir}")
        folder_cookie_layout.addWidget(self.lbl_save_folder, stretch=1)

        self.btn_browse = QPushButton("📁 Browse")
        self.btn_browse.clicked.connect(self.browse_save_folder)
        folder_cookie_layout.addWidget(self.btn_browse)

        self.btn_open_folder = QPushButton("📂 Open Folder")
        self.btn_open_folder.clicked.connect(self.open_save_folder)
        folder_cookie_layout.addWidget(self.btn_open_folder)

        folder_cookie_layout.addSpacing(16)
        self.lbl_cookie_status = QLabel("Cookie: Not Found")
        folder_cookie_layout.addWidget(self.lbl_cookie_status)

        self.btn_import_cookie = QPushButton("🍪 Import Cookie")
        self.btn_import_cookie.clicked.connect(self.import_cookie)
        folder_cookie_layout.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton("Clear Cookie")
        self.btn_clear_cookie.clicked.connect(self.clear_cookie)
        folder_cookie_layout.addWidget(self.btn_clear_cookie)
        bottom_layout.addLayout(folder_cookie_layout)

        # Download & Cancel Buttons
        actions_progress_layout = QHBoxLayout()
        self.btn_download_all = QPushButton("⬇ Download All")
        self.btn_download_all.setStyleSheet(
            "background-color: #e1306c; font-weight: bold; font-size: 14px; padding: 10px 20px;"
        )
        self.btn_download_all.clicked.connect(self.start_download)
        actions_progress_layout.addWidget(self.btn_download_all)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_current_operation)
        actions_progress_layout.addWidget(self.btn_cancel)

        # Custom Modern Progress Bar
        self.progress_bar = ModernProgressBar(self)
        self.progress_bar.setValue(0)
        actions_progress_layout.addWidget(self.progress_bar, stretch=1)
        bottom_layout.addLayout(actions_progress_layout)

        self.lbl_status_msg = QLabel("Ready")
        self.lbl_status_msg.setStyleSheet("color: #888888;")
        bottom_layout.addWidget(self.lbl_status_msg)

        main_layout.addLayout(bottom_layout)

    # --------------------------------------------------------------------------
    # Card Management & Selection
    # --------------------------------------------------------------------------
    def add_card(self, item_data: dict):
        card = MediaCard(item_data, lang=self.current_lang, parent=self)
        card.selection_changed.connect(self.update_selection_counter)
        card.delete_requested.connect(self.remove_card)

        # Insert before the stretch item
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.media_cards.append(card)
        self.update_selection_counter()

    def remove_card(self, card: MediaCard):
        if card in self.media_cards:
            self.media_cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
            self.update_selection_counter()

    def clear_media_grid(self):
        """Safely clears all media cards from the scroll layout."""
        for card in self.media_cards:
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.media_cards.clear()
        self.update_selection_counter()

    def update_selection_counter(self):
        selected_count = sum(1 for c in self.media_cards if c.is_selected())
        total_count = len(self.media_cards)
        self.lbl_selected_counter.setText(
            f"Selected: {selected_count} / {total_count} items"
        )

    def on_select_all(self):
        select_state = any(not c.is_selected() for c in self.media_cards)
        for card in self.media_cards:
            card.set_selected(select_state)
        self.update_selection_counter()

    def on_delete_selected(self):
        for card in list(self.media_cards):
            if card.is_selected():
                self.remove_card(card)

    def on_clear_completed(self):
        for card in list(self.media_cards):
            if card.is_completed():
                self.remove_card(card)

    # --------------------------------------------------------------------------
    # Inspection Pipeline
    # --------------------------------------------------------------------------
    def start_inspection(self):
        raw_targets = self.url_container.get_targets()
        if not raw_targets:
            QMessageBox.warning(
                self, "Warning", "Please enter at least one valid Instagram URL."
            )
            return

        self.clear_media_grid()
        self.progress_bar.setValue(0)
        self.btn_inspect.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status_msg.setText("Inspecting media URLs...")

        self.inspect_worker = InspectWorker(
            raw_targets, cookie_path=self.cookie_file_path, parent=self
        )
        self.inspect_worker.item_found.connect(self.add_card)
        self.inspect_worker.progress.connect(self.progress_bar.setValue)
        self.inspect_worker.status.connect(self.lbl_status_msg.setText)
        self.inspect_worker.finished.connect(self.on_inspection_finished)
        self.inspect_worker.error.connect(self.on_inspection_error)
        self.inspect_worker.start()

    def on_inspection_finished(self, total_found: int):
        self.progress_bar.setValue(100)
        self.btn_inspect.setEnabled(True)
        self.btn_download_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.lbl_status_msg.setText(
            f"Inspection completed! Found {total_found} items ready."
        )

    def on_inspection_error(self, err_msg: str):
        self.lbl_status_msg.setText(f"Error: {err_msg}")

    # --------------------------------------------------------------------------
    # Download Pipeline
    # --------------------------------------------------------------------------
    def start_download(self):
        items_to_download = [
            c.get_download_payload() for c in self.media_cards if c.is_selected()
        ]
        if not items_to_download:
            # If none selected, download all ready cards
            items_to_download = [c.get_download_payload() for c in self.media_cards]

        if not items_to_download:
            QMessageBox.information(
                self, "Info", "No media items available to download."
            )
            return

        self.progress_bar.setValue(0)
        self.btn_download_all.setEnabled(False)
        self.btn_inspect.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        self.download_worker = DownloadWorker(
            items_to_download,
            download_dir=self.download_dir,
            cookie_path=self.cookie_file_path,
            parent=self,
        )
        self.download_worker.progress.connect(self.progress_bar.setValue)
        self.download_worker.card_status.connect(self.on_download_card_status)
        self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.start()

    def on_download_card_status(self, item_id: str, status_code: str):
        for card in self.media_cards:
            if card.item_id == item_id:
                card.update_status(status_code)

    def on_download_finished(self):
        self.progress_bar.setValue(100)
        self.btn_download_all.setEnabled(True)
        self.btn_inspect.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.lbl_status_msg.setText("Download process completed.")

    def cancel_current_operation(self):
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.stop()
            self.lbl_status_msg.setText("Cancelling inspection...")
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.stop()
            self.lbl_status_msg.setText("Cancelling download...")

    # --------------------------------------------------------------------------
    # Cookie & Directory Management
    # --------------------------------------------------------------------------
    def import_cookie(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Netscape cookies.txt", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            ok, msg = CookieManager.sanitize_and_save(
                file_path, self.cookie_file_path, lang=self.current_lang
            )
            if ok:
                QMessageBox.information(self, "Success", msg)
            else:
                QMessageBox.warning(self, "Error", msg)
            self.update_cookie_status_ui()

    def clear_cookie(self):
        if os.path.exists(self.cookie_file_path):
            try:
                os.remove(self.cookie_file_path)
            except Exception as e:
                logger.error(f"Error removing cookie: {e}")
        self.update_cookie_status_ui()

    def update_cookie_status_ui(self):
        if CookieManager.is_cookie_valid(self.cookie_file_path):
            self.lbl_cookie_status.setText("Cookie: Connected (Instagram)")
            self.lbl_cookie_status.setStyleSheet("color: #43d692; font-weight: bold;")
        else:
            self.lbl_cookie_status.setText("Cookie: Not Found")
            self.lbl_cookie_status.setStyleSheet("color: #ff4d4d; font-weight: bold;")

    def browse_save_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Download Directory", self.download_dir
        )
        if folder:
            self.download_dir = folder
            self.lbl_save_folder.setText(f"Save Folder: {self.download_dir}")

    def open_save_folder(self):
        os.makedirs(self.download_dir, exist_ok=True)
        if os.name == "nt":
            os.startfile(self.download_dir)

    # --------------------------------------------------------------------------
    # Localization
    # --------------------------------------------------------------------------
    def on_language_changed(self):
        self.current_lang = self.combo_lang.currentData()
        self.retranslate_ui()

    def retranslate_ui(self):
        t = TRANSLATIONS.get(self.current_lang, TRANSLATIONS["en"])
        self.btn_inspect.setText(t.get("inspect_media", "🔍 Inspect Media"))
        self.btn_clear_text.setText(t.get("clear_textbox", "✕ Clear Textbox"))
        self.btn_select_all.setText(t.get("select_all", "☑ Select All (Ctrl+A)"))
        self.btn_delete_selected.setText(
            t.get("delete_selected", "🗑 Delete Selected (Del)")
        )
        self.btn_clear_completed.setText(t.get("clear_completed", "🧹 Clear Completed"))
        self.btn_download_all.setText(t.get("download_all", "⬇ Download All"))
        self.btn_cancel.setText(t.get("cancel", "⏹ Cancel"))
        self.btn_browse.setText(t.get("browse", "📁 Browse"))
        self.btn_open_folder.setText(t.get("open_folder", "📂 Open Folder"))
        self.btn_import_cookie.setText(t.get("import_cookie", "🍪 Import Cookie"))
        self.btn_clear_cookie.setText(t.get("clear_cookie", "Clear Cookie"))
        self.update_selection_counter()
