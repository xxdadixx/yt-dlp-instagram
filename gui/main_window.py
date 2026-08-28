"""
gui/main_window.py - Main Application Window
"""

import os
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.constants import (
    APP_NAME,
    DEFAULT_DOWNLOAD_DIR,
    QUALITY_PRESETS,
)
from core.cookie_manager import CookieManager
from core.download_worker import DownloadWorker
from core.inspect_worker import InspectWorker
from gui.styles import MAIN_STYLESHEET
from gui.widgets.media_card import MediaCard
from gui.widgets.modern_progress_bar import ModernProgressBar
from gui.widgets.no_scroll_combo import NoScrollComboBox
from gui.widgets.url_chip_input import UrlChipInput


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 840)
        self.setMinimumSize(960, 680)

        self.cookie_manager = CookieManager()
        self.inspect_worker: InspectWorker | None = None
        self.download_worker: DownloadWorker | None = None
        self.media_cards: list[MediaCard] = []

        self.download_path = DEFAULT_DOWNLOAD_DIR
        os.makedirs(self.download_path, exist_ok=True)

        self._init_ui()
        self._setup_shortcuts()
        self._check_cookie_status()

    def _init_ui(self):
        self.setStyleSheet(MAIN_STYLESHEET)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # --- Section 1: URL Input Area ---
        input_group = QFrame(self)
        input_group.setObjectName("CardGroup")
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(8)

        group_title = QLabel(
            "Instagram URLs (Supports Posts, Reels, Carousels, Stories & Highlights)",
            self,
        )
        group_title.setObjectName("SectionTitle")
        input_layout.addWidget(group_title)

        self.url_container = UrlChipInput(self)
        input_layout.addWidget(self.url_container)

        # Action Buttons
        btn_bar = QHBoxLayout()
        self.btn_inspect = QPushButton("🔍 Inspect Media", self)
        self.btn_inspect.setObjectName("PrimaryButton")
        self.btn_inspect.clicked.connect(self.start_inspection)

        self.btn_clear_input = QPushButton("✕ Clear Textbox", self)
        self.btn_clear_input.setObjectName("SecondaryButton")
        self.btn_clear_input.clicked.connect(self.url_container.clear_all)

        btn_bar.addWidget(self.btn_inspect)
        btn_bar.addWidget(self.btn_clear_input)
        btn_bar.addStretch()
        input_layout.addLayout(btn_bar)

        main_layout.addWidget(input_group)

        # --- Section 2: Media Queue / Grid Area ---
        queue_group = QFrame(self)
        queue_group.setObjectName("CardGroup")
        queue_layout = QVBoxLayout(queue_group)
        queue_layout.setContentsMargins(12, 12, 12, 12)
        queue_layout.setSpacing(8)

        # Queue Toolbar
        queue_toolbar = QHBoxLayout()
        queue_title = QLabel("Media Queue Cards (Media Grid Inspector)", self)
        queue_title.setObjectName("SectionTitle")
        queue_toolbar.addWidget(queue_title)

        self.btn_select_all = QPushButton("☑ Select All (Ctrl+A)", self)
        self.btn_select_all.clicked.connect(self.select_all_cards)
        queue_toolbar.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton("🗑 Delete Selected (Del)", self)
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        queue_toolbar.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton("🧹 Clear Completed", self)
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        queue_toolbar.addWidget(self.btn_clear_completed)

        queue_toolbar.addStretch()

        self.lbl_selection_count = QLabel("Selected: 0 / 0 items", self)
        self.lbl_selection_count.setObjectName("MutedLabel")
        queue_toolbar.addWidget(self.lbl_selection_count)

        queue_layout.addLayout(queue_toolbar)

        # Scroll Area for Cards
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        queue_layout.addWidget(self.scroll_area)

        main_layout.addWidget(queue_group, stretch=1)

        # --- Section 3: Bottom Control & Download Bar ---
        bottom_bar = QFrame(self)
        bottom_bar.setObjectName("BottomBar")
        bottom_layout = QVBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(12, 8, 12, 8)
        bottom_layout.setSpacing(6)

        ctrl_row = QHBoxLayout()
        self.lbl_save_folder = QLabel(f"Save Folder: {self.download_path}", self)
        ctrl_row.addWidget(self.lbl_save_folder)

        btn_browse = QPushButton("📁 Browse", self)
        btn_browse.clicked.connect(self.browse_folder)
        ctrl_row.addWidget(btn_browse)

        btn_open_folder = QPushButton("📂 Open Folder", self)
        btn_open_folder.clicked.connect(self.open_save_folder)
        ctrl_row.addWidget(btn_open_folder)

        ctrl_row.addSpacing(16)

        self.lbl_cookie_status = QLabel("Cookie: Disconnected", self)
        ctrl_row.addWidget(self.lbl_cookie_status)

        self.btn_import_cookie = QPushButton("🔑 Import Cookie", self)
        self.btn_import_cookie.clicked.connect(self.import_cookie)
        ctrl_row.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton("🗑 Clear Cookie", self)
        self.btn_clear_cookie.clicked.connect(self.clear_cookie)
        ctrl_row.addWidget(self.btn_clear_cookie)

        ctrl_row.addStretch()

        self.btn_download_all = QPushButton("⬇ Download All", self)
        self.btn_download_all.setObjectName("DownloadButton")
        self.btn_download_all.clicked.connect(self.start_download)
        ctrl_row.addWidget(self.btn_download_all)

        self.btn_cancel = QPushButton("⏹ Cancel", self)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_current_task)
        ctrl_row.addWidget(self.btn_cancel)

        bottom_layout.addLayout(ctrl_row)

        # Modern Progress Bar & Status Text
        self.progress_bar = ModernProgressBar(self)
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Ready", self)
        self.lbl_status.setObjectName("StatusLabel")
        bottom_layout.addWidget(self.lbl_status)

        main_layout.addWidget(bottom_bar)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+A"), self, self.select_all_cards)
        QShortcut(QKeySequence("Delete"), self, self.delete_selected_cards)

    # =========================================================================
    # Media Inspection Flow
    # =========================================================================

    def start_inspection(self):
        targets = self.url_container.get_targets()
        if not targets:
            self.lbl_status.setText(
                "Please enter or paste at least one valid Instagram URL."
            )
            return

        self.clear_media_grid()
        self.btn_inspect.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Starting inspection...")

        cookie_file = self.cookie_manager.get_cookie_file_path()
        cookie_str = self.cookie_manager.get_cookie_header_string()

        self.inspect_worker = InspectWorker(
            targets=targets,
            cookie_file=cookie_file,
            cookie_str=cookie_str,
        )
        self.inspect_worker.progress.connect(self.on_inspection_progress)
        self.inspect_worker.card_ready.connect(self.add_card)
        self.inspect_worker.finished_inspection.connect(self.on_inspection_finished)
        self.inspect_worker.error.connect(self.on_inspection_error)
        self.inspect_worker.start()

    def on_inspection_progress(self, val: int, msg: str):
        self.progress_bar.setValue(val)
        self.lbl_status.setText(msg)

    def on_inspection_finished(self, total_count: int):
        self.progress_bar.setValue(100)
        self.lbl_status.setText(
            f"Inspection completed! Found {total_count} items ready."
        )
        self.btn_inspect.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.update_selection_counter()

    def on_inspection_error(self, err_msg: str):
        self.lbl_status.setText(f"Inspection Warning: {err_msg}")

    # =========================================================================
    # Media Card Grid Management
    # =========================================================================

    def add_card(self, item_data: dict):
        card = MediaCard(item_data, parent=self.cards_container)
        card.deleted.connect(lambda: self.remove_card(card))
        card.selection_changed.connect(self.update_selection_counter)

        # Insert before stretch item at the bottom
        insert_idx = max(0, self.cards_layout.count() - 1)
        self.cards_layout.insertWidget(insert_idx, card)
        self.media_cards.append(card)
        self.update_selection_counter()

    def remove_card(self, card: MediaCard):
        if card in self.media_cards:
            self.media_cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
            self.update_selection_counter()

    def clear_media_grid(self):
        for card in list(self.media_cards):
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.media_cards.clear()
        self.update_selection_counter()

    def select_all_cards(self):
        for card in self.media_cards:
            card.set_selected(True)
        self.update_selection_counter()

    def delete_selected_cards(self):
        for card in list(self.media_cards):
            if card.is_selected():
                self.remove_card(card)

    def clear_completed_cards(self):
        for card in list(self.media_cards):
            if card.get_status().lower() == "completed":
                self.remove_card(card)

    def update_selection_counter(self):
        selected = sum(1 for c in self.media_cards if c.is_selected())
        total = len(self.media_cards)
        self.lbl_selection_count.setText(f"Selected: {selected} / {total} items")

    # =========================================================================
    # Download Flow
    # =========================================================================

    def start_download(self):
        selected_cards = [c for c in self.media_cards if c.is_selected()]
        items_to_download = selected_cards if selected_cards else self.media_cards

        if not items_to_download:
            self.lbl_status.setText("No items in queue to download.")
            return

        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)

        # Spawn DownloadWorker
        payload = [c.get_payload() for c in items_to_download]
        self.download_worker = DownloadWorker(
            items=payload,
            download_dir=self.download_path,
            cookie_file=self.cookie_manager.get_cookie_file_path(),
        )
        self.download_worker.progress.connect(self.on_download_progress)
        self.download_worker.item_completed.connect(self.on_item_download_completed)
        self.download_worker.finished_download.connect(self.on_download_finished)
        self.download_worker.start()

    def on_download_progress(self, val: int, msg: str):
        self.progress_bar.setValue(val)
        self.lbl_status.setText(msg)

    def on_item_download_completed(self, item_id: str, success: bool):
        for card in self.media_cards:
            if card.item_id == item_id:
                card.set_status("Completed" if success else "Failed")
                break

    def on_download_finished(self, success_count: int, fail_count: int):
        self.progress_bar.setValue(100)
        self.lbl_status.setText(
            f"Download finished! Completed: {success_count}, Failed: {fail_count}"
        )
        self.btn_download_all.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def cancel_current_task(self):
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            self.lbl_status.setText("Cancelling inspection...")
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.lbl_status.setText("Cancelling download...")

    # =========================================================================
    # Directory & Cookie Configuration
    # =========================================================================

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Save Folder", self.download_path
        )
        if folder:
            self.download_path = folder
            self.lbl_save_folder.setText(f"Save Folder: {self.download_path}")

    def open_save_folder(self):
        if os.path.exists(self.download_path):
            os.startfile(self.download_path)

    def _check_cookie_status(self):
        if self.cookie_manager.has_valid_cookie():
            self.lbl_cookie_status.setText("Cookie: Connected (Instagram)")
            self.lbl_cookie_status.setStyleSheet("color: #4cd964;")
        else:
            self.lbl_cookie_status.setText("Cookie: Disconnected")
            self.lbl_cookie_status.setStyleSheet("color: #ff3b30;")

    def import_cookie(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Instagram Cookies",
            "",
            "Text/Cookie Files (*.txt *.json);;All Files (*.*)",
        )
        if file_path:
            self.cookie_manager.import_from_file(file_path)
            self._check_cookie_status()

    def clear_cookie(self):
        self.cookie_manager.clear_cookies()
        self._check_cookie_status()
