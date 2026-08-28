import os
import sys
import subprocess
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSize
from PyQt6.QtGui import (
    QIcon,
    QFont,
    QColor,
    QDesktopServices,
    QClipboard,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QScrollArea,
    QFrame,
    QFileDialog,
    QMessageBox,
    QSpacerItem,
    QSizePolicy,
    QApplication,
)

from config.constants import (
    APP_NAME,
    APP_VERSION,
    APP_USER_MODEL_ID,
    QUALITY_PRESETS,
    DEFAULT_HEADERS,
    DESKTOP_UA,
    IG_APP_ID,
)
from core.inspect_worker import InspectWorker
from core.download_worker import DownloadWorker
from core.cookie_manager import CookieManager
from gui.widgets.url_chip_input import URLChipInput
from gui.widgets.media_card import MediaCard
from gui.widgets.modern_progress_bar import ModernProgressBar
from gui.widgets.no_scroll_combo import NoScrollComboBox
from gui.styles import DARK_STYLESHEET


class MainWindow(QMainWindow):
    """
    Main Application Window for Instagram Pro Downloader - Studio Inspector.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: List[MediaCard] = []
        self.inspect_worker: Optional[InspectWorker] = None
        self.download_worker: Optional[DownloadWorker] = None
        self.cookie_manager = CookieManager()
        self.cookie_str: str = (
            self.cookie_manager.get_cookie_string()
            if hasattr(self.cookie_manager, "get_cookie_string")
            else ""
        )
        self.cookie_file: str = (
            self.cookie_manager.get_cookie_file_path()
            if hasattr(self.cookie_manager, "get_cookie_file_path")
            else ""
        )

        self.save_folder: str = os.path.abspath("downloads")
        os.makedirs(self.save_folder, exist_ok=True)

        self._last_clipboard_text: str = ""

        self.init_ui()
        self.setup_clipboard_monitor()
        self.update_cookie_status()

    def init_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}")
        self.resize(1020, 820)
        self.setMinimumSize(920, 680)
        self.setStyleSheet(DARK_STYLESHEET)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ----------------------------------------------------
        # TOP SECTION: URL Input & Controls
        # ----------------------------------------------------
        top_group = QFrame(self)
        top_group.setObjectName("TopGroupFrame")
        top_layout = QVBoxLayout(top_group)
        top_layout.setContentsMargins(12, 12, 12, 12)
        top_layout.setSpacing(10)

        # Header Row
        header_row = QHBoxLayout()
        lbl_group_title = QLabel(
            "Instagram URLs (Supports Posts, Reels, Carousels, Stories Highlights)",
            self,
        )
        lbl_group_title.setObjectName("GroupTitleLabel")
        header_row.addWidget(lbl_group_title)
        header_row.addStretch()

        self.combo_lang = NoScrollComboBox(self)
        self.combo_lang.addItems(["us English", "th ไทย"])
        self.combo_lang.setCurrentIndex(0)
        header_row.addWidget(self.combo_lang)
        top_layout.addLayout(header_row)

        # URL Chip Input Widget
        self.url_container = URLChipInput(self)
        top_layout.addWidget(self.url_container)

        # Action & Monitor Row
        action_row = QHBoxLayout()
        self.chk_clipboard = QCheckBox(
            "Auto Clipboard Monitor (Auto-paste when IG link is copied)", self
        )
        self.chk_clipboard.setChecked(True)
        action_row.addWidget(self.chk_clipboard)
        action_row.addStretch()

        self.btn_inspect = QPushButton("🔍 Inspect Media", self)
        self.btn_inspect.setObjectName("PrimaryActionButton")
        self.btn_inspect.clicked.connect(self.start_inspection)
        action_row.addWidget(self.btn_inspect)

        self.btn_clear_text = QPushButton("✕ Clear Textbox", self)
        self.btn_clear_text.setObjectName("SecondaryActionButton")
        self.btn_clear_text.clicked.connect(self.url_container.clear)
        action_row.addWidget(self.btn_clear_text)

        top_layout.addLayout(action_row)

        # Progress Bar
        self.progress_bar = ModernProgressBar(self)
        self.progress_bar.setValue(0)
        top_layout.addWidget(self.progress_bar)

        main_layout.addWidget(top_group)

        # ----------------------------------------------------
        # MIDDLE SECTION: Media Queue Cards Grid
        # ----------------------------------------------------
        mid_group = QFrame(self)
        mid_group.setObjectName("MidGroupFrame")
        mid_layout = QVBoxLayout(mid_group)
        mid_layout.setContentsMargins(12, 12, 12, 12)
        mid_layout.setSpacing(8)

        # Queue Header Row
        queue_header_row = QHBoxLayout()
        lbl_queue_title = QLabel("Media Queue Cards (Media Grid Inspector)", self)
        lbl_queue_title.setObjectName("GroupTitleLabel")
        queue_header_row.addWidget(lbl_queue_title)

        self.btn_select_all = QPushButton("☑ Select All (Ctrl+A)", self)
        self.btn_select_all.clicked.connect(self.select_all_cards)
        queue_header_row.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton("🗑 Delete Selected (Del)", self)
        self.btn_delete_selected.setObjectName("DeleteButton")
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        queue_header_row.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton("☑ Clear Completed", self)
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        queue_header_row.addWidget(self.btn_clear_completed)

        queue_header_row.addStretch()

        self.lbl_selected_count = QLabel("Selected: 0 / 0 items", self)
        self.lbl_selected_count.setObjectName("CounterLabel")
        queue_header_row.addWidget(self.lbl_selected_count)
        mid_layout.addLayout(queue_header_row)

        # Scroll Area for Cards
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_widget = QWidget()
        self.media_grid_layout = QVBoxLayout(self.scroll_widget)
        self.media_grid_layout.setContentsMargins(4, 4, 4, 4)
        self.media_grid_layout.setSpacing(8)
        self.media_grid_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_widget)
        mid_layout.addWidget(self.scroll_area)

        main_layout.addWidget(mid_group, stretch=1)

        # ----------------------------------------------------
        # BOTTOM SECTION: Controls & Status Bar
        # ----------------------------------------------------
        bot_group = QFrame(self)
        bot_group.setObjectName("BottomGroupFrame")
        bot_layout = QVBoxLayout(bot_group)
        bot_layout.setContentsMargins(12, 12, 12, 12)
        bot_layout.setSpacing(10)

        # Config Row
        config_row = QHBoxLayout()
        self.lbl_save_folder = QLabel(f"Save Folder: {self.save_folder}", self)
        config_row.addWidget(self.lbl_save_folder)

        self.btn_browse = QPushButton("📁 Browse", self)
        self.btn_browse.clicked.connect(self.browse_save_folder)
        config_row.addWidget(self.btn_browse)

        self.btn_open_folder = QPushButton("📂 Open Folder", self)
        self.btn_open_folder.clicked.connect(self.open_save_folder)
        config_row.addWidget(self.btn_open_folder)

        config_row.addSpacing(20)
        self.lbl_cookie_status = QLabel("Cookie: Not Connected", self)
        config_row.addWidget(self.lbl_cookie_status)

        self.btn_import_cookie = QPushButton("🔑 Import Cookie", self)
        self.btn_import_cookie.clicked.connect(self.import_cookie)
        config_row.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton("🗑 Clear Cookie", self)
        self.btn_clear_cookie.clicked.connect(self.clear_cookie)
        config_row.addWidget(self.btn_clear_cookie)

        bot_layout.addLayout(config_row)

        # Download Buttons Row
        dl_row = QHBoxLayout()
        self.btn_download_all = QPushButton("⬇ Download All", self)
        self.btn_download_all.setObjectName("DownloadAllButton")
        self.btn_download_all.clicked.connect(self.start_download)
        dl_row.addWidget(self.btn_download_all, stretch=1)

        self.btn_cancel_dl = QPushButton("Cancel", self)
        self.btn_cancel_dl.clicked.connect(self.cancel_download)
        dl_row.addWidget(self.btn_cancel_dl)
        bot_layout.addLayout(dl_row)

        # Status & Toast Row
        status_row = QHBoxLayout()
        self.lbl_status = QLabel("Ready", self)
        self.lbl_status.setObjectName("StatusMessageLabel")
        status_row.addWidget(self.lbl_status)

        status_row.addStretch()

        self.lbl_toast = QLabel("", self)
        self.lbl_toast.setObjectName("ToastLabel")
        status_row.addWidget(self.lbl_toast)
        bot_layout.addLayout(status_row)

        main_layout.addWidget(bot_group)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+A"), self, self.select_all_cards)
        QShortcut(QKeySequence("Delete"), self, self.delete_selected_cards)

    # --------------------------------------------------------
    # Clipboard & Toast Helpers
    # --------------------------------------------------------
    def setup_clipboard_monitor(self) -> None:
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_changed)

    def on_clipboard_changed(self) -> None:
        if not self.chk_clipboard.isChecked():
            return
        text = self.clipboard.text().strip()
        if text and text != self._last_clipboard_text:
            self._last_clipboard_text = text
            if "instagram.com" in text.lower():
                self.url_container.add_url_chip(text)
                self.show_toast("Instagram link detected and pasted from clipboard")

    def show_toast(
        self, message: str, is_error: bool = False, duration_ms: int = 4000
    ) -> None:
        color = "#ff6b6b" if is_error else "#43d692"
        self.lbl_toast.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.lbl_toast.setText(message)
        QTimer.singleShot(duration_ms, lambda: self.lbl_toast.setText(""))

    # --------------------------------------------------------
    # Media Inspection Workflow
    # --------------------------------------------------------
    def start_inspection(self) -> None:
        """Starts the media inspection worker thread."""
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            self.btn_inspect.setText("🔍 Inspect Media")
            self.lbl_status.setText("Inspection cancelled by user.")
            return

        targets = self.url_container.get_targets()
        if not targets:
            self.show_toast("Please add at least one Instagram URL.", is_error=True)
            self.lbl_status.setText("No URLs in queue.")
            return

        # ModernProgressBar only implements setValue; avoid setRange
        self.progress_bar.setValue(0)
        self.clear_media_grid()

        self.btn_inspect.setText("⏹ Cancel")
        self.lbl_status.setText("Starting inspection...")

        self.inspect_worker = InspectWorker(
            targets=targets,
            cookie_str=self.cookie_str,
            cookie_file=self.cookie_file,
            quality_preset="best_video",
            parent=self,
        )

        self.inspect_worker.item_found.connect(self.add_card)
        self.inspect_worker.progress.connect(self.on_inspection_progress)
        self.inspect_worker.status_message.connect(self.on_status_message)
        self.inspect_worker.finished.connect(self.on_inspection_finished)
        self.inspect_worker.error.connect(self.on_inspection_error)
        self.inspect_worker.start()

    def add_card(self, item_data: Dict[str, Any]) -> None:
        """Instantiates and adds a new MediaCard to the grid."""
        card = MediaCard(item_data, parent=self)
        if hasattr(card, "deleted"):
            card.deleted.connect(lambda: self.remove_card(card))
        if hasattr(card, "selection_changed"):
            card.selection_changed.connect(self.update_selection_counter)

        count = self.media_grid_layout.count()
        if count > 0:
            self.media_grid_layout.insertWidget(count - 1, card)
        else:
            self.media_grid_layout.addWidget(card)

        self.cards.append(card)
        self.update_selection_counter()

    def remove_card(self, card: MediaCard) -> None:
        """Removes and disposes of a MediaCard."""
        if card in self.cards:
            self.cards.remove(card)
        self.media_grid_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self.update_selection_counter()

    def clear_media_grid(self) -> None:
        """Safely clears all media cards from the layout."""
        for card in list(self.cards):
            self.media_grid_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        self.update_selection_counter()

    def on_inspection_progress(self, val: int) -> None:
        self.progress_bar.setValue(val)

    def on_inspection_finished(self, total_count: int) -> None:
        self.progress_bar.setValue(100)
        self.btn_inspect.setText("🔍 Inspect Media")
        self.lbl_status.setText(
            f"Inspection completed! Found {total_count} items ready."
        )
        self.show_toast(f"Found {total_count} items ready.")

    def on_inspection_error(self, err_msg: str) -> None:
        self.btn_inspect.setText("🔍 Inspect Media")
        self.lbl_status.setText(f"Error: {err_msg}")
        self.show_toast(err_msg, is_error=True)

    def on_status_message(self, msg: str) -> None:
        self.lbl_status.setText(msg)

    # --------------------------------------------------------
    # Grid Selection & Management Actions
    # --------------------------------------------------------
    def update_selection_counter(self) -> None:
        total = len(self.cards)
        selected = sum(1 for c in self.cards if getattr(c, "is_selected", True))
        self.lbl_selected_count.setText(f"Selected: {selected} / {total} items")

    def select_all_cards(self) -> None:
        for card in self.cards:
            if hasattr(card, "set_selected"):
                card.set_selected(True)
        self.update_selection_counter()

    def delete_selected_cards(self) -> None:
        for card in list(self.cards):
            if getattr(card, "is_selected", False):
                self.remove_card(card)
        self.update_selection_counter()

    def clear_completed_cards(self) -> None:
        for card in list(self.cards):
            if (
                getattr(card, "is_finished", False)
                or getattr(card, "status", "") == "finished"
            ):
                self.remove_card(card)
        self.update_selection_counter()

    # --------------------------------------------------------
    # Cookie & Folder Management
    # --------------------------------------------------------
    def update_cookie_status(self) -> None:
        has_cookie = bool(
            self.cookie_str or (self.cookie_file and os.path.exists(self.cookie_file))
        )
        if has_cookie:
            self.lbl_cookie_status.setText("Cookie: Connected (Instagram)")
            self.lbl_cookie_status.setStyleSheet("color: #43d692; font-weight: bold;")
        else:
            self.lbl_cookie_status.setText("Cookie: Not Connected")
            self.lbl_cookie_status.setStyleSheet("color: #888888;")

    def import_cookie(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Instagram Cookie",
            "",
            "Cookie Files (*.txt *.json);;All Files (*.*)",
        )
        if file_path:
            if hasattr(self.cookie_manager, "import_cookie_file"):
                self.cookie_manager.import_cookie_file(file_path)
                self.cookie_file = file_path
                self.cookie_str = self.cookie_manager.get_cookie_string()
            self.update_cookie_status()
            self.show_toast("Cookie imported successfully!")

    def clear_cookie(self) -> None:
        if hasattr(self.cookie_manager, "clear_cookies"):
            self.cookie_manager.clear_cookies()
        self.cookie_str = ""
        self.cookie_file = ""
        self.update_cookie_status()
        self.show_toast("Cookie cleared.")

    def browse_save_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self.save_folder
        )
        if folder:
            self.save_folder = folder
            self.lbl_save_folder.setText(f"Save Folder: {self.save_folder}")

    def open_save_folder(self) -> None:
        if os.path.exists(self.save_folder):
            if sys.platform == "win32":
                os.startfile(self.save_folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.save_folder])
            else:
                subprocess.Popen(["xdg-open", self.save_folder])

    # --------------------------------------------------------
    # Download Execution
    # --------------------------------------------------------
    def start_download(self) -> None:
        selected_cards = [c for c in self.cards if getattr(c, "is_selected", True)]
        if not selected_cards:
            self.show_toast("No items selected for download.", is_error=True)
            return

        self.btn_download_all.setEnabled(False)
        self.lbl_status.setText(f"Starting download for {len(selected_cards)} items...")

        items_payload = [
            c.get_item_data() if hasattr(c, "get_item_data") else c.item_data
            for c in selected_cards
        ]
        self.download_worker = DownloadWorker(
            items=items_payload,
            save_folder=self.save_folder,
            cookie_str=self.cookie_str,
            cookie_file=self.cookie_file,
            parent=self,
        )
        if hasattr(self.download_worker, "progress"):
            self.download_worker.progress.connect(self.on_download_progress)
        if hasattr(self.download_worker, "item_finished"):
            self.download_worker.item_finished.connect(self.on_download_item_finished)
        if hasattr(self.download_worker, "finished"):
            self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.start()

    def cancel_download(self) -> None:
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.btn_download_all.setEnabled(True)
            self.lbl_status.setText("Download cancelled.")

    def on_download_progress(self, val: int) -> None:
        self.progress_bar.setValue(val)

    def on_download_item_finished(self, item_id: str, success: bool) -> None:
        for card in self.cards:
            if (
                getattr(card, "item_id", None) == item_id
                or getattr(card, "shortcode", None) == item_id
            ):
                if hasattr(card, "set_status"):
                    card.set_status("finished" if success else "error")

    def on_download_finished(self, success_count: int) -> None:
        self.btn_download_all.setEnabled(True)
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"Download complete! {success_count} items downloaded.")
        self.show_toast(f"Downloaded {success_count} items.")
