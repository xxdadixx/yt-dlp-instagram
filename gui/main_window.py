"""
gui/main_window.py - Main GUI Window for Instagram Pro Downloader & Studio Inspector.
Handles worker coordination, persistent settings, cookie management, and media card grid lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

try:
    from PyQt6.QtCore import (
        QSize,
        Qt,
        QThread,
        QTimer,
        QUrl,
        pyqtSignal,
    )
    from PyQt6.QtGui import (
        QClipboard,
        QColor,
        QDesktopServices,
        QFont,
        QIcon,
        QKeySequence,
        QShortcut,
    )
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpacerItem,
        QVBoxLayout,
        QWidget,
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

    class QWidget:  # type: ignore
        def __init__(self, parent=None):
            pass

        def setStyleSheet(self, *a):
            pass

        def setObjectName(self, *a):
            pass

        def resize(self, *a):
            pass

        def setMinimumSize(self, *a):
            pass

    class QMainWindow(QWidget):
        def setCentralWidget(self, *a):
            pass

        def setWindowTitle(self, *a):
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

        def addSpacing(self, *a):
            pass

        def insertWidget(self, *a):
            pass

        def removeWidget(self, *a):
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

        def text(self):
            return ""

        def setToolTip(self, *a):
            pass

        def setStyleSheet(self, *a):
            pass

    class QPushButton(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self.clicked = MagicSignal()

        def setText(self, *a):
            pass

        def text(self):
            return ""

        def setIcon(self, *a):
            pass

        def setIconSize(self, *a):
            pass

        def setEnabled(self, *a):
            pass

        def setToolTip(self, *a):
            pass

        def setStyleSheet(self, *a):
            pass

    class QCheckBox(QWidget):
        def __init__(self, text="", parent=None):
            super().__init__(parent)
            self._checked = False
            self.stateChanged = MagicSignal()

        def setChecked(self, c: bool):
            self._checked = c
            self.stateChanged.emit(c)

        def isChecked(self) -> bool:
            return self._checked

        def setText(self, *a):
            pass

        def text(self):
            return ""

    class QScrollArea(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)

        def setWidgetResizable(self, *a):
            pass

        def setFrameShape(self, *a):
            pass

        def setWidget(self, *a):
            pass

    class QFrame(QWidget):
        class Shape:
            NoFrame = 0

    class QKeySequence:
        def __init__(self, s=""):
            pass

    class QShortcut:
        def __init__(self, seq, parent, callback=None):
            pass

    class QFileDialog:
        @staticmethod
        def getOpenFileName(*a, **kw):
            return ("", "")

        @staticmethod
        def getExistingDirectory(*a, **kw):
            return ""

    class QApplication:
        @staticmethod
        def clipboard():
            class Clip:
                dataChanged = MagicSignal()

                def text(self):
                    return ""

            return Clip()

    class QTimer:
        @staticmethod
        def singleShot(ms, f):
            pass


from config.constants import (
    APP_NAME,
    APP_USER_MODEL_ID,
    APP_VERSION,
    DEFAULT_HEADERS,
    QUALITY_PRESETS,
)
from core.cookie_manager import CookieManager
from core.inspect_worker import InspectWorker

try:
    from config.translations import TRANSLATIONS
except ImportError:
    TRANSLATIONS = {}

try:
    from gui.icons import get_icon
except ImportError:

    def get_icon(name: str, color: str = "#ffffff", size: int = 18):
        return None


try:
    from core.download_worker import DownloadWorker
except ImportError:

    class DownloadWorker:  # type: ignore
        def __init__(self, *a, **kw):
            pass

        def isRunning(self):
            return False

        def start(self):
            pass

        def cancel(self):
            pass


try:
    from gui.widgets.url_chip_input import URLChipInput
except ImportError:

    class URLChipInput(QWidget):  # type: ignore
        def __init__(self, parent=None):
            super().__init__(parent)
            self._urls: List[str] = []

        def get_targets(self) -> List[str]:
            return list(self._urls)

        def add_url_chip(self, u: str) -> None:
            self._urls.append(u)

        def clear(self) -> None:
            self._urls.clear()


try:
    from gui.widgets.media_card import MediaCard
except ImportError:

    class MediaCard(QWidget):  # type: ignore
        def __init__(self, item_data, parent=None):
            super().__init__(parent)
            self.item_data = item_data
            self.is_selected = True
            self.deleted = MagicSignal()
            self.selection_changed = MagicSignal()

        def set_selected(self, s: bool):
            self.is_selected = s

        def deleteLater(self):
            pass

        def setParent(self, p):
            pass


try:
    from gui.widgets.modern_progress_bar import ModernProgressBar
except ImportError:

    class ModernProgressBar(QWidget):  # type: ignore
        def __init__(self, parent=None):
            super().__init__(parent)
            self._val = 0

        def setValue(self, val: int) -> None:
            self._val = val

        def value(self) -> int:
            return self._val


try:
    from gui.widgets.no_scroll_combo import NoScrollComboBox
except ImportError:

    class NoScrollComboBox(QWidget):  # type: ignore
        def __init__(self, parent=None):
            super().__init__(parent)

        def addItems(self, *a):
            pass

        def setCurrentIndex(self, *a):
            pass

        def currentIndex(self):
            return 0

        def currentText(self):
            return ""


try:
    from gui.styles import DARK_STYLESHEET
except ImportError:
    DARK_STYLESHEET = ""

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Main Application Window for Instagram Pro Downloader - Studio Inspector.
    Provides complete URL entry, progress visualization, media card grid management,
    persistent link settings, and seamless cookie importing.
    """

    SETTINGS_FILE = os.path.join("config", "settings.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: List[MediaCard] = []
        self.inspect_worker: Optional[InspectWorker] = None
        self.download_worker: Optional[DownloadWorker] = None

        # Cookie Management
        self.cookie_manager = CookieManager()
        self.cookie_str: str = self.cookie_manager.get_cookie_string()
        self.cookie_file: str = self.cookie_manager.get_cookie_file_path()

        # Defaults before settings load
        self.save_folder: str = os.path.abspath("downloads")
        self.current_lang: str = "en"
        self.auto_clipboard: bool = True
        self.quality_preset: str = "best_video"
        self._last_clipboard_text: str = ""

        # Load persistent settings
        self.load_settings()
        os.makedirs(self.save_folder, exist_ok=True)

        self.init_ui()
        self.apply_loaded_settings()
        self.setup_clipboard_monitor()
        self.update_cookie_status()

    # --------------------------------------------------------
    # Settings Persistence
    # --------------------------------------------------------
    def load_settings(self) -> None:
        """Loads user preferences and link settings from settings.json."""
        if os.path.exists(self.SETTINGS_FILE):
            try:
                with open(self.SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.save_folder = data.get("save_folder", self.save_folder)
                    self.current_lang = data.get("language", self.current_lang)
                    self.auto_clipboard = bool(
                        data.get("auto_clipboard", self.auto_clipboard)
                    )
                    self.quality_preset = data.get(
                        "quality_preset", self.quality_preset
                    )
            except Exception as e:
                logger.debug(f"Failed to load settings: {e}")

    def save_settings(self) -> None:
        """Persists current link settings and user preferences to settings.json."""
        try:
            os.makedirs(os.path.dirname(self.SETTINGS_FILE), exist_ok=True)
            if hasattr(self, "chk_clipboard") and hasattr(
                self.chk_clipboard, "isChecked"
            ):
                self.auto_clipboard = self.chk_clipboard.isChecked()

            settings_payload = {
                "save_folder": self.save_folder,
                "language": self.current_lang,
                "auto_clipboard": self.auto_clipboard,
                "quality_preset": self.quality_preset,
            }
            with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings_payload, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save settings: {e}")

    def apply_loaded_settings(self) -> None:
        """Applies loaded settings into UI widgets."""
        if hasattr(self, "chk_clipboard"):
            self.chk_clipboard.setChecked(self.auto_clipboard)
        if hasattr(self, "lbl_save_folder"):
            self.lbl_save_folder.setText(f"Save Folder: {self.save_folder}")
        if hasattr(self, "combo_lang"):
            lang_idx = 1 if self.current_lang == "th" else 0
            self.combo_lang.setCurrentIndex(lang_idx)
            self.on_language_changed(lang_idx)

    def closeEvent(self, event) -> None:
        """Saves settings on window close."""
        self.save_settings()
        super().closeEvent(event) if hasattr(super(), "closeEvent") else None

    # --------------------------------------------------------
    # UI Initialization
    # --------------------------------------------------------
    def init_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME}")
        self.resize(1040, 860)
        self.setMinimumSize(920, 700)
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
        top_layout.setContentsMargins(14, 14, 14, 14)
        top_layout.setSpacing(10)

        # 1. Header Row
        header_row = QHBoxLayout()
        self.lbl_group_title = QLabel(
            "Instagram URLs (Supports Posts, Reels, Carousels, Stories Highlights)",
            self,
        )
        self.lbl_group_title.setObjectName("GroupTitleLabel")
        header_row.addWidget(self.lbl_group_title)
        header_row.addStretch()

        self.combo_lang = NoScrollComboBox(self)
        self.combo_lang.addItems(["English (US)", "ไทย (TH)"])
        self.combo_lang.setCurrentIndex(0)
        if hasattr(self.combo_lang, "currentIndexChanged"):
            self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        header_row.addWidget(self.combo_lang)
        top_layout.addLayout(header_row)

        # 2. URL Input Field (Prominent Multi-line / Chip Input)
        self.url_container = URLChipInput(self)
        top_layout.addWidget(self.url_container)

        # 3. Action & Monitor Row
        action_row = QHBoxLayout()
        self.chk_clipboard = QCheckBox(
            "Auto Clipboard Monitor (Auto-paste when IG link is copied)", self
        )
        self.chk_clipboard.setChecked(self.auto_clipboard)
        if hasattr(self.chk_clipboard, "stateChanged") and hasattr(
            self.chk_clipboard.stateChanged, "connect"
        ):
            self.chk_clipboard.stateChanged.connect(self._on_clipboard_check_changed)
        action_row.addWidget(self.chk_clipboard)
        action_row.addStretch()

        self.btn_inspect = QPushButton("Inspect Media", self)
        self.btn_inspect.setObjectName("PrimaryActionButton")
        self._set_button_icon(self.btn_inspect, "search", "#ffffff", 16)
        self.btn_inspect.clicked.connect(self.start_inspection)
        action_row.addWidget(self.btn_inspect)

        self.btn_clear_text = QPushButton("Clear Textbox", self)
        self.btn_clear_text.setObjectName("SecondaryActionButton")
        self._set_button_icon(self.btn_clear_text, "clear", "#cbd5e1", 14)
        self.btn_clear_text.clicked.connect(self.url_container.clear)
        action_row.addWidget(self.btn_clear_text)

        top_layout.addLayout(action_row)

        # 4. Progress Bar (ModernProgressBar implements setValue)
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
        mid_layout.setContentsMargins(14, 14, 14, 14)
        mid_layout.setSpacing(10)

        # Queue Header Row
        queue_header_row = QHBoxLayout()
        self.lbl_queue_title = QLabel("Media Queue Cards (Media Grid Inspector)", self)
        self.lbl_queue_title.setObjectName("GroupTitleLabel")
        queue_header_row.addWidget(self.lbl_queue_title)

        self.btn_select_all = QPushButton("Select All (Ctrl+A)", self)
        self._set_button_icon(self.btn_select_all, "select_all", "#4ade80", 15)
        self.btn_select_all.clicked.connect(self.select_all_cards)
        queue_header_row.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton("Delete Selected (Del)", self)
        self.btn_delete_selected.setObjectName("DeleteButton")
        self._set_button_icon(self.btn_delete_selected, "trash", "#f87171", 15)
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        queue_header_row.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton("Clear Completed", self)
        self._set_button_icon(self.btn_clear_completed, "check_double", "#93c5fd", 15)
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
        bot_layout.setContentsMargins(14, 14, 14, 14)
        bot_layout.setSpacing(10)

        # Config Row
        config_row = QHBoxLayout()
        self.lbl_save_folder = QLabel(f"Save Folder: {self.save_folder}", self)
        config_row.addWidget(self.lbl_save_folder)

        self.btn_browse = QPushButton("Browse", self)
        self._set_button_icon(self.btn_browse, "folder", "#fcd34d", 15)
        self.btn_browse.clicked.connect(self.browse_save_folder)
        config_row.addWidget(self.btn_browse)

        self.btn_open_folder = QPushButton("Open Folder", self)
        self._set_button_icon(self.btn_open_folder, "folder_open", "#fcd34d", 15)
        self.btn_open_folder.clicked.connect(self.open_save_folder)
        config_row.addWidget(self.btn_open_folder)

        config_row.addSpacing(20)
        self.lbl_cookie_status = QLabel("Cookie: Not Connected", self)
        config_row.addWidget(self.lbl_cookie_status)

        self.btn_import_cookie = QPushButton("Import Cookie", self)
        self._set_button_icon(self.btn_import_cookie, "key", "#fbbf24", 15)
        self.btn_import_cookie.clicked.connect(self.import_cookie)
        config_row.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton("Clear Cookie", self)
        self._set_button_icon(self.btn_clear_cookie, "trash", "#fb7185", 15)
        self.btn_clear_cookie.clicked.connect(self.clear_cookie)
        config_row.addWidget(self.btn_clear_cookie)

        bot_layout.addLayout(config_row)

        # Download Buttons Row
        dl_row = QHBoxLayout()
        self.btn_download_all = QPushButton("Download All", self)
        self.btn_download_all.setObjectName("DownloadAllButton")
        self._set_button_icon(self.btn_download_all, "download", "#ffffff", 16)
        self.btn_download_all.clicked.connect(self.start_download)
        dl_row.addWidget(self.btn_download_all, stretch=1)

        self.btn_cancel_dl = QPushButton("Cancel", self)
        self._set_button_icon(self.btn_cancel_dl, "cancel", "#e2e8f0", 14)
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

    def _set_button_icon(
        self,
        button: QPushButton,
        icon_name: str,
        color: str = "#ffffff",
        size: int = 16,
    ) -> None:
        """Helper to safely set vector QIcon on QPushButton."""
        icon = get_icon(icon_name, color=color, size=size)
        if icon and hasattr(button, "setIcon"):
            button.setIcon(icon)
            if hasattr(button, "setIconSize"):
                button.setIconSize(QSize(size, size))

    def _on_clipboard_check_changed(self, state: Any = None) -> None:
        """Handles changes to clipboard monitor checkbox."""
        if hasattr(self, "chk_clipboard") and hasattr(self.chk_clipboard, "isChecked"):
            self.auto_clipboard = self.chk_clipboard.isChecked()
        self.save_settings()

    # --------------------------------------------------------
    # Language Localization
    # --------------------------------------------------------
    def on_language_changed(self, index: int) -> None:
        lang = "th" if index == 1 else "en"
        self.current_lang = lang
        self.save_settings()

        tr = TRANSLATIONS.get(lang, {})
        if not tr:
            return

        self.lbl_group_title.setText(tr.get("title", self.lbl_group_title.text()))
        self.chk_clipboard.setText(tr.get("auto_clipboard", self.chk_clipboard.text()))
        self.btn_inspect.setText(tr.get("inspect_media", self.btn_inspect.text()))
        self.btn_clear_text.setText(tr.get("clear_textbox", self.btn_clear_text.text()))
        self.lbl_queue_title.setText(tr.get("queue_title", self.lbl_queue_title.text()))
        self.btn_select_all.setText(tr.get("select_all", self.btn_select_all.text()))
        self.btn_delete_selected.setText(
            tr.get("delete_selected", self.btn_delete_selected.text())
        )
        self.btn_clear_completed.setText(
            tr.get("clear_completed", self.btn_clear_completed.text())
        )
        self.btn_browse.setText(tr.get("browse", self.btn_browse.text()))
        self.btn_open_folder.setText(tr.get("open_folder", self.btn_open_folder.text()))
        self.btn_import_cookie.setText(
            tr.get("import_cookie", self.btn_import_cookie.text())
        )
        self.btn_clear_cookie.setText(
            tr.get("clear_cookie", self.btn_clear_cookie.text())
        )
        self.btn_download_all.setText(
            tr.get("download_all", self.btn_download_all.text())
        )
        self.btn_cancel_dl.setText(tr.get("cancel", self.btn_cancel_dl.text()))
        self.update_selection_counter()
        self.update_cookie_status()

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
                self.show_toast("Instagram link detected and added from clipboard")

    def show_toast(
        self, message: str, is_error: bool = False, duration_ms: int = 4000
    ) -> None:
        color = "#f87171" if is_error else "#4ade80"
        self.lbl_toast.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.lbl_toast.setText(message)
        QTimer.singleShot(duration_ms, lambda: self.lbl_toast.setText(""))

    # --------------------------------------------------------
    # Media Inspection Workflow
    # --------------------------------------------------------
    def start_inspection(self) -> None:
        """Starts the media inspection worker thread and clears previous inputs."""
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            self._set_button_icon(self.btn_inspect, "search", "#ffffff", 16)
            self.btn_inspect.setText("Inspect Media")
            self.lbl_status.setText("Inspection cancelled by user.")
            return

        targets = self.url_container.get_targets()
        if not targets:
            self.show_toast("Please add at least one Instagram URL.", is_error=True)
            self.lbl_status.setText("No URLs in queue.")
            return

        # Automatically clear previously entered / inspected links from input field
        self.url_container.clear()

        # Reset progress bar to 0 and clear prior media grid cards
        self.progress_bar.setValue(0)
        self.clear_media_grid()

        self._set_button_icon(self.btn_inspect, "stop", "#ffffff", 16)
        self.btn_inspect.setText("Cancel")
        self.lbl_status.setText("Starting inspection...")

        self.inspect_worker = InspectWorker(
            targets=targets,
            cookie_str=self.cookie_str,
            cookie_file=self.cookie_file,
            quality_preset=self.quality_preset,
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
        """Removes and safely disposes of a MediaCard."""
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
        self._set_button_icon(self.btn_inspect, "search", "#ffffff", 16)
        self.btn_inspect.setText("Inspect Media")
        self.lbl_status.setText(
            f"Inspection completed! Found {total_count} items ready."
        )
        self.show_toast(f"Found {total_count} items ready.")

    def on_inspection_error(self, err_msg: str) -> None:
        self._set_button_icon(self.btn_inspect, "search", "#ffffff", 16)
        self.btn_inspect.setText("Inspect Media")
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
        has_cookie = self.cookie_manager.has_cookies() or bool(
            self.cookie_str or (self.cookie_file and os.path.exists(self.cookie_file))
        )
        if has_cookie:
            self.lbl_cookie_status.setText("Cookie: Connected (Instagram)")
            self.lbl_cookie_status.setStyleSheet("color: #4ade80; font-weight: bold;")
        else:
            self.lbl_cookie_status.setText("Cookie: Not Connected")
            self.lbl_cookie_status.setStyleSheet("color: #94a3b8;")

    def import_cookie(self) -> None:
        """Opens file dialog and imports cookie file in Netscape, JSON, or text format."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Instagram Cookie",
            "",
            "Cookie Files (*.txt *.json);;All Files (*.*)",
        )
        if file_path:
            success = self.cookie_manager.import_cookie_file(file_path)
            if success:
                self.cookie_file = self.cookie_manager.get_cookie_file_path()
                self.cookie_str = self.cookie_manager.get_cookie_string()
                self.update_cookie_status()
                self.save_settings()
                self.show_toast("Cookie imported successfully!")
            else:
                self.show_toast(
                    "Failed to import cookie file. Invalid format.", is_error=True
                )

    def clear_cookie(self) -> None:
        """Clears all stored cookies."""
        self.cookie_manager.clear_cookies()
        self.cookie_str = ""
        self.cookie_file = ""
        self.update_cookie_status()
        self.save_settings()
        self.show_toast("Cookie cleared.")

    def browse_save_folder(self) -> None:
        """Opens directory picker and updates persistent save directory."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self.save_folder
        )
        if folder:
            self.save_folder = folder
            self.lbl_save_folder.setText(f"Save Folder: {self.save_folder}")
            self.save_settings()

    def open_save_folder(self) -> None:
        if os.path.exists(self.save_folder):
            if sys.platform == "win32":
                os.startfile(self.save_folder)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.save_folder])
            else:
                subprocess.Popen(["xdg-open", self.save_folder])
        else:
            self.show_toast("Save folder does not exist.", is_error=True)

    # --------------------------------------------------------
    # Download Execution Flow
    # --------------------------------------------------------
    def start_download(self) -> None:
        selected_cards = [c for c in self.cards if getattr(c, "is_selected", True)]
        if not selected_cards:
            self.show_toast("No media items selected for download.", is_error=True)
            return

        items_to_download = [c.get_item_data() for c in selected_cards]
        self.btn_download_all.setEnabled(False)
        self.lbl_status.setText(f"Downloading {len(items_to_download)} items...")

        self.download_worker = DownloadWorker(
            items=items_to_download,
            save_folder=self.save_folder,
            cookie_file=self.cookie_file,
            cookie_str=self.cookie_str,
            parent=self,
        )
        if hasattr(self.download_worker, "progress"):
            self.download_worker.progress.connect(self.on_download_progress)
        if hasattr(self.download_worker, "item_started"):
            self.download_worker.item_started.connect(self.on_download_item_started)
        if hasattr(self.download_worker, "item_finished"):
            self.download_worker.item_finished.connect(self.on_download_item_finished)
        if hasattr(self.download_worker, "status_message"):
            self.download_worker.status_message.connect(self.on_status_message)
        if hasattr(self.download_worker, "finished"):
            self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.start()

    def on_download_item_started(self, item_id: str) -> None:
        for card in self.cards:
            if (
                getattr(card, "item_id", None) == item_id
                or getattr(card, "shortcode", None) == item_id
            ):
                if hasattr(card, "set_status"):
                    card.set_status("downloading")

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
