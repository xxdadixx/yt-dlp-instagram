"""
gui/main_window.py - Instagram Pro Studio Main Window with Scaled High-DPI Workspace,
Icon-Only Controls, Chronological Sorting, and Tab Navigation Counters.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set

# Suppress Windows OLE clipboard mutex retry logs
os.environ["QT_LOGGING_RULES"] = (
    "qt.text.font.db=false;qt.text.*=false;qt.qpa.mime=false;qt.qpa.*=false"
)

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config.constants import APP_NAME
from config.translations import TRANSLATIONS
from core.cookie_manager import CookieManager
from core.download_worker import DownloadWorker
from core.inspect_worker import InspectWorker
from core.parser import extract_instagram_urls, shortcode_to_id
from gui.icons import get_icon
from gui.styles import DARK_STYLESHEET
from gui.widgets.log_viewer_widget import LogViewerWidget
from gui.widgets.media_card import MediaCard
from gui.widgets.modern_progress_bar import ModernProgressBar
from gui.widgets.no_scroll_combo import NoScrollComboBox
from gui.widgets.url_chip_input import URLChipInput
from utils.file_utils import get_app_dir
from utils.logger import QtLogHandler

logger = logging.getLogger(__name__)


def extract_chronological_key(item_data: Dict[str, Any]) -> int:
    """Extracts a monotonic integer timestamp or snowflake ID from media payload (higher = newer)."""
    if not isinstance(item_data, dict):
        return 0

    ts = (
        item_data.get("taken_at_timestamp")
        or item_data.get("taken_at")
        or item_data.get("timestamp")
        or item_data.get("date")
    )
    if ts:
        try:
            return int(ts)
        except (ValueError, TypeError):
            pass

    raw_id = item_data.get("id") or item_data.get("pk") or item_data.get("media_id")
    if raw_id:
        try:
            clean_id = str(raw_id).split("_")[0]
            val = int(clean_id)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

    shortcode = item_data.get("shortcode") or item_data.get("code")
    if shortcode and isinstance(shortcode, str):
        decoded = shortcode_to_id(shortcode)
        if decoded is not None and decoded > 0:
            return decoded

    return 0


class MainWindow(QMainWindow):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.cards: List[MediaCard] = []
        self._card_id_set: Set[str] = set()
        self._card_sort_keys: List[int] = []
        self._last_selected_index: int = -1
        self._last_selected_card: Optional[MediaCard] = None
        self.inspect_worker: Optional[InspectWorker] = None
        self.download_worker: Optional[DownloadWorker] = None

        # Defaults
        self.save_folder: str = os.path.abspath("downloads")
        self.current_lang: str = "en"
        self.auto_clipboard: bool = True
        self.profile_mode: str = "all"
        self.quality_preset: str = "best_video"
        self._last_clipboard_text: str = ""
        self._queue_scroll_anim: Optional[QPropertyAnimation] = None
        self._saved_geometry_hex: str = ""
        self._is_maximized: bool = False

        self.load_settings()
        os.makedirs(self.save_folder, exist_ok=True)

        # Logging and UI initialization
        self._setup_logging()
        self.init_ui()

        # Cookie Manager
        self.cookie_manager = CookieManager()
        self.cookie_str: str = self.cookie_manager.get_cookie_string()
        self.cookie_file: str = self.cookie_manager.get_cookie_file_path() or ""

        self.apply_translations()
        self.setup_clipboard_monitor()
        self.update_cookie_status()

    def _setup_crawl_limit_selector(self) -> None:
        self.combo_batch_limit.blockSignals(True)
        self.combo_batch_limit.clear()

        presets = [
            ("36 items (Fast)", 36),
            ("72 items (Safe)", 72),
            ("120 items (Deep)", 120),
            ("240 items (Macro-Paced)", 240),
            ("480 items (Deep Crawl)", 480),
            ("960 items (High Capacity)", 960),
            ("All Available (No Limit)", 0),
        ]

        for label, limit_val in presets:
            self.combo_batch_limit.addItem(label, userData=limit_val)

        self.combo_batch_limit.setSizeAdjustPolicy(
            NoScrollComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.combo_batch_limit.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self.combo_batch_limit.setMinimumWidth(185)
        self.combo_batch_limit.setCurrentIndex(1)
        self.combo_batch_limit.blockSignals(False)

    def get_all_media_cards(self) -> list[MediaCard]:
        return self.cards

    def tr_text(self, key: str, **kwargs) -> str:
        lang_dict = TRANSLATIONS.get(self.current_lang, TRANSLATIONS["en"])
        val = lang_dict.get(key, TRANSLATIONS["en"].get(key, key))
        return val.format(**kwargs) if kwargs else val

    def init_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(920, 640)
        self.setStyleSheet(DARK_STYLESHEET)

        if self._saved_geometry_hex:
            try:
                self.restoreGeometry(
                    QByteArray.fromHex(self._saved_geometry_hex.encode("utf-8"))
                )
            except Exception as e:
                logger.debug("Failed to restore window geometry: %s", e)
                self.resize(1120, 800)
        else:
            self.resize(1120, 800)

        if self._is_maximized:
            self.showMaximized()

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # 1. Top Bar: App Brand + Cookie Status + Language
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 0)
        top_bar.setSpacing(12)

        self.lbl_app_brand = QLabel("✨ Instagram Pro Studio", self)
        self.lbl_app_brand.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.lbl_app_brand.setStyleSheet("color: #FFFFFF; letter-spacing: 0.3px;")
        top_bar.addWidget(self.lbl_app_brand)

        top_bar.addStretch()

        self.lbl_cookie_status = QLabel(self)
        self.lbl_cookie_status.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        top_bar.addWidget(self.lbl_cookie_status)

        # Import Cookie Button (Icon Only - Scaled)
        self.btn_import_cookie = QPushButton(self)
        self.btn_import_cookie.setObjectName("GlassActionButton")
        self._set_button_icon(self.btn_import_cookie, "key", "#FCAF45", 16)
        self.btn_import_cookie.setFixedSize(38, 34)
        self.btn_import_cookie.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import_cookie.clicked.connect(self.import_cookie)
        top_bar.addWidget(self.btn_import_cookie)

        self.combo_lang = NoScrollComboBox(self)
        self.combo_lang.addItems(["English (EN)", "ภาษาไทย (TH)"])
        self.combo_lang.setFixedHeight(34)
        self.combo_lang.setCurrentIndex(1 if self.current_lang == "th" else 0)
        self.combo_lang.currentIndexChanged.connect(self._on_lang_changed)
        top_bar.addWidget(self.combo_lang)

        main_layout.addLayout(top_bar)

        # 2. URL Input Bar
        self.url_container = URLChipInput(self)
        self.url_container.urls_changed.connect(self._on_urls_list_updated)
        main_layout.addWidget(self.url_container.input_widget)

        # 3. Action Strip: Auto-Paste + Profile Mode + Crawl Limit + Inspect
        action_strip = QHBoxLayout()
        action_strip.setContentsMargins(2, 0, 2, 0)
        action_strip.setSpacing(12)

        self.chk_clipboard = QCheckBox(self)
        self.chk_clipboard.setChecked(self.auto_clipboard)
        self.chk_clipboard.setFont(QFont("Segoe UI", 10))
        self.chk_clipboard.setMinimumWidth(200)
        self.chk_clipboard.stateChanged.connect(self._on_clipboard_toggle)
        action_strip.addWidget(self.chk_clipboard)

        action_strip.addStretch()

        self.lbl_profile_filter = QLabel(self)
        self.lbl_profile_filter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.lbl_profile_filter.setStyleSheet("color: #A0A0B2;")
        action_strip.addWidget(self.lbl_profile_filter)

        self.combo_profile_mode = NoScrollComboBox(self)
        self.combo_profile_mode.setFixedHeight(38)
        self.combo_profile_mode.setMinimumWidth(160)
        self.combo_profile_mode.setSizeAdjustPolicy(
            NoScrollComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.combo_profile_mode.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        mode_idx = (
            0
            if self.profile_mode == "all"
            else (1 if self.profile_mode == "reels" else 2)
        )
        self.combo_profile_mode.addItems(["All Media", "Reels Only", "Photos Only"])
        self.combo_profile_mode.setCurrentIndex(mode_idx)
        self.combo_profile_mode.currentIndexChanged.connect(
            self._on_profile_mode_changed
        )
        action_strip.addWidget(self.combo_profile_mode)

        self.lbl_batch_limit = QLabel("Crawl Limit:", self)
        self.lbl_batch_limit.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.lbl_batch_limit.setStyleSheet("color: #A0A0B2;")
        action_strip.addWidget(self.lbl_batch_limit)

        self.combo_batch_limit = NoScrollComboBox(self)
        self.combo_batch_limit.setFixedHeight(38)
        self._setup_crawl_limit_selector()
        action_strip.addWidget(self.combo_batch_limit)

        # Inspect Button (Icon Only - Scaled)
        self.btn_inspect = QPushButton(self)
        self.btn_inspect.setObjectName("PrimaryActionButton")
        self.btn_inspect.setFixedSize(44, 38)
        self._set_button_icon(self.btn_inspect, "search", "#FFFFFF", 18)
        self.btn_inspect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_inspect.clicked.connect(self.start_inspection)
        action_strip.addWidget(self.btn_inspect)

        main_layout.addLayout(action_strip)

        # 4. Progress Bar
        self.progress_bar = ModernProgressBar(self)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        # 5. Switchable Tab Widget
        self.tab_widget = QTabWidget(self)

        # Tab 0: Media Queue Panel
        queue_tab = QWidget()
        queue_layout = QVBoxLayout(queue_tab)
        queue_layout.setContentsMargins(12, 10, 12, 10)
        queue_layout.setSpacing(8)

        queue_bar = QHBoxLayout()
        self.lbl_queue_count = QLabel(self)
        self.lbl_queue_count.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_queue_count.setStyleSheet("color: #FFFFFF;")
        self.lbl_selected_count = self.lbl_queue_count
        queue_bar.addWidget(self.lbl_queue_count)

        queue_bar.addStretch()

        # Select All (Icon Only - Scaled)
        self.btn_select_all = QPushButton(self)
        self.btn_select_all.setObjectName("GlassActionButton")
        self.btn_select_all.setFixedSize(36, 32)
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_select_all, "select_all", "#10B981", 16)
        self.btn_select_all.clicked.connect(self.toggle_select_all)
        queue_bar.addWidget(self.btn_select_all)

        # Delete Selected (Icon Only - Scaled)
        self.btn_delete_selected = QPushButton(self)
        self.btn_delete_selected.setObjectName("DestructiveButton")
        self.btn_delete_selected.setFixedSize(36, 32)
        self.btn_delete_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_delete_selected, "trash", "#FF6B6B", 15)
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        queue_bar.addWidget(self.btn_delete_selected)

        # Clear Completed (Icon Only - Scaled)
        self.btn_clear_completed = QPushButton(self)
        self.btn_clear_completed.setObjectName("GlassActionButton")
        self.btn_clear_completed.setFixedSize(36, 32)
        self.btn_clear_completed.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_clear_completed, "check_double", "#70C5FF", 16)
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        queue_bar.addWidget(self.btn_clear_completed)

        queue_layout.addLayout(queue_bar)

        self.scroll_area = QScrollArea(queue_tab)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_widget = QWidget()
        self.media_grid_layout = QVBoxLayout(self.scroll_widget)
        self.media_grid_layout.setContentsMargins(4, 4, 4, 4)
        self.media_grid_layout.setSpacing(8)
        self.media_grid_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_widget)
        queue_layout.addWidget(self.scroll_area, stretch=1)

        self.tab_widget.addTab(queue_tab, "Media Queue (0)")

        # Tab 1: URL Links List
        self.tab_widget.addTab(self.url_container.list_widget, "URL Links (0)")

        # Tab 2: Activity Logs
        self.log_viewer = LogViewerWidget(self)
        self.tab_widget.addTab(self.log_viewer, "Activity Logs")
        if hasattr(self, "log_handler") and self.log_handler:
            self.log_handler.emitter.log_record_emitted.connect(
                self.log_viewer.append_log
            )

        main_layout.addWidget(self.tab_widget, stretch=1)

        # 6. Bottom Bento Bar: Folder Controls + Status + Download Action
        bot_bar = QHBoxLayout()
        bot_bar.setContentsMargins(2, 0, 2, 0)
        bot_bar.setSpacing(10)

        # Change Folder Button (Icon Only - Scaled)
        self.btn_change_folder = QPushButton(self)
        self.btn_change_folder.setObjectName("GlassActionButton")
        self.btn_change_folder.setFixedSize(38, 34)
        self.btn_change_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_change_folder, "folder", "#FCAF45", 16)
        self.btn_change_folder.clicked.connect(self.browse_save_folder)
        bot_bar.addWidget(self.btn_change_folder)

        # Open Folder Button (Icon Only - Scaled)
        self.btn_open_folder = QPushButton(self)
        self.btn_open_folder.setObjectName("GlassActionButton")
        self.btn_open_folder.setFixedSize(38, 34)
        self.btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_open_folder, "folder_open", "#F56040", 16)
        self.btn_open_folder.clicked.connect(self.open_save_folder)
        bot_bar.addWidget(self.btn_open_folder)

        self.lbl_status = QLabel(self)
        self.lbl_status.setFont(QFont("Segoe UI", 9))
        self.lbl_status.setStyleSheet("color: #A0A0B2;")
        bot_bar.addWidget(self.lbl_status, 1)

        self.lbl_toast = QLabel("", self)
        self.lbl_toast.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        bot_bar.addWidget(self.lbl_toast)

        # Download Action Button (Icon Only - Scaled)
        self.btn_download_all = QPushButton(self)
        self.btn_download_all.setObjectName("DownloadAllButton")
        self.btn_download_all.setFixedSize(48, 38)
        self.btn_download_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_button_icon(self.btn_download_all, "download", "#FFFFFF", 18)
        self.btn_download_all.clicked.connect(self.start_download)
        bot_bar.addWidget(self.btn_download_all)

        main_layout.addLayout(bot_bar)

        # Global Selection & Action Shortcuts
        QShortcut(QKeySequence("Ctrl+A"), self, self.select_all_cards)
        QShortcut(QKeySequence("Ctrl+D"), self, self.deselect_all_cards)
        QShortcut(QKeySequence("Ctrl+I"), self, self.invert_selection)
        QShortcut(QKeySequence("Space"), self, self.toggle_active_selection)
        QShortcut(QKeySequence("Delete"), self, self.delete_selected_cards)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self.start_download)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, self.start_download)

    def clear_media_grid(self) -> None:
        self.clear_all_cards()

    def _setup_logging(self) -> None:
        self.log_handler = QtLogHandler()
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", "%H:%M:%S"
        )
        self.log_handler.setFormatter(formatter)
        self.log_handler.setLevel(logging.DEBUG)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(self.log_handler)

    def apply_translations(self) -> None:
        self.lbl_app_brand.setText(self.tr_text("app_title"))
        self.btn_import_cookie.setToolTip(self.tr_text("dialog_import_cookie"))
        self.url_container.input_edit.setPlaceholderText(
            self.tr_text("input_placeholder")
        )
        self.chk_clipboard.setText(self.tr_text("auto_clipboard"))
        self.lbl_profile_filter.setText(self.tr_text("profile_filter_label"))

        curr_mode_idx = self.combo_profile_mode.currentIndex()
        self.combo_profile_mode.blockSignals(True)
        self.combo_profile_mode.clear()
        self.combo_profile_mode.addItems(
            [
                self.tr_text("filter_all"),
                self.tr_text("filter_reels"),
                self.tr_text("filter_photos"),
            ]
        )
        self.combo_profile_mode.setCurrentIndex(
            curr_mode_idx if curr_mode_idx >= 0 else 0
        )
        self.combo_profile_mode.blockSignals(False)

        is_inspecting = self.inspect_worker and self.inspect_worker.isRunning()
        self.btn_inspect.setToolTip(
            self.tr_text("inspect_cancel" if is_inspecting else "inspect_media")
        )

        self.btn_select_all.setToolTip(self.tr_text("btn_select_all"))
        self.btn_delete_selected.setToolTip(self.tr_text("btn_delete"))
        self.btn_clear_completed.setToolTip(self.tr_text("btn_clear_completed"))
        self.btn_change_folder.setToolTip(self.tr_text("btn_change_folder"))
        self.btn_open_folder.setToolTip(self.tr_text("btn_open_folder"))

        is_downloading = self.download_worker and self.download_worker.isRunning()
        self.btn_download_all.setToolTip(
            self.tr_text(
                "btn_cancel_download" if is_downloading else "btn_download_selected"
            )
        )

        if not hasattr(self, "_custom_status") or not self._custom_status:
            self.lbl_status.setText(self.tr_text("status_ready"))

        self.update_selection_counter()
        self._on_urls_list_updated()
        self.update_cookie_status()

    def _set_button_icon(
        self, button: QPushButton, name: str, color: str = "#FFFFFF", size: int = 16
    ) -> None:
        icon = get_icon(name, color=color, size=size)
        if icon:
            button.setIcon(icon)
            button.setIconSize(QSize(size, size))

    def setup_clipboard_monitor(self) -> None:
        cb = QApplication.clipboard()
        if cb and hasattr(cb, "dataChanged"):
            cb.dataChanged.connect(self._on_clipboard_data_changed)

    def _on_clipboard_toggle(self, state: int) -> None:
        self.auto_clipboard = state == 2 or state is True
        self.save_settings()

    def _on_clipboard_data_changed(self) -> None:
        if not self.auto_clipboard:
            return
        QTimer.singleShot(350, self._process_clipboard)

    def _process_clipboard(self) -> None:
        if not self.auto_clipboard:
            return
        try:
            cb = QApplication.clipboard()
            if not cb:
                return
            text = cb.text().strip()
            if not text or text == self._last_clipboard_text:
                return

            self._last_clipboard_text = text
            urls = extract_instagram_urls(text)
            if urls:
                for u in urls:
                    self.url_container.add_url_chip(u)
                self.show_toast(self.tr_text("toast_pasted", count=len(urls)))
                self.tab_widget.setCurrentIndex(1)
        except Exception:
            pass

    def _on_urls_list_updated(self) -> None:
        count = self.url_container.count()
        self.tab_widget.setTabText(1, self.tr_text("tab_url_list", count=count))
        self.url_container.lbl_list_count.setText(
            self.tr_text("tab_url_list", count=count)
        )

    def _on_profile_mode_changed(self, idx: int) -> None:
        modes = ["all", "reels", "photos"]
        self.profile_mode = modes[idx] if idx < len(modes) else "all"
        self.save_settings()

    def start_inspection(self) -> None:
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
            self._set_button_icon(self.btn_inspect, "search", "#FFFFFF", 18)
            self.btn_inspect.setToolTip(self.tr_text("inspect_media"))
            self.lbl_status.setText(self.tr_text("status_ready"))
            self._update_action_button_states()
            return

        targets = self.url_container.get_targets()
        if not targets:
            self.show_toast(self.tr_text("toast_no_urls"), is_error=True)
            return

        self.url_container.clear()
        self.progress_bar.setValue(0)
        self._set_button_icon(self.btn_inspect, "stop", "#FFFFFF", 18)
        self.btn_inspect.setToolTip(self.tr_text("inspect_cancel"))
        self.lbl_status.setText(self.tr_text("status_inspecting"))

        self.tab_widget.setCurrentIndex(0)

        raw_limit_data = (
            self.combo_batch_limit.currentData()
            if hasattr(self, "combo_batch_limit")
            else None
        )
        selected_limit = int(raw_limit_data) if raw_limit_data is not None else 72

        self.inspect_worker = InspectWorker(
            targets=targets,
            cookie_str=self.cookie_manager.get_cookie_string(),
            cookie_file=self.cookie_manager.get_cookie_file_path(),
            profile_mode=self.profile_mode,
            quality_preset=self.quality_preset,
            max_items_per_profile=selected_limit,
            parent=self,
        )
        self.inspect_worker.item_found.connect(self.add_card)
        self.inspect_worker.progress.connect(self.progress_bar.setValue)
        self.inspect_worker.status_message.connect(self.lbl_status.setText)
        self.inspect_worker.status_message.connect(
            lambda msg: logger.info(f"[Inspect] {msg}")
        )
        self.inspect_worker.error.connect(
            lambda err: logger.error(f"[Inspect Error] {err}")
        )
        self.inspect_worker.error_occurred.connect(
            lambda err: logger.error(f"[Inspect Error] {err}")
        )
        self.inspect_worker.finished.connect(self.on_inspection_finished)

        logger.info(
            f"Starting inspection for {len(targets)} target(s) with mode='{self.profile_mode}' (limit: {selected_limit})"
        )
        self.inspect_worker.start()
        self._update_action_button_states()

    def add_card(self, item_data: Dict[str, Any]) -> None:
        """
        Inserts media item into UI in O(log N) time using binary search insertion
        and O(1) deduplication to prevent UI layout freezing.
        """
        import bisect

        new_id = str(
            item_data.get("id")
            or item_data.get("shortcode")
            or item_data.get("url")
            or ""
        )
        c_idx = str(item_data.get("carousel_index", ""))
        dedup_key = f"{new_id}::{c_idx}" if new_id else ""

        if dedup_key and dedup_key in self._card_id_set:
            return

        card = MediaCard(item_data, parent=self)
        card.deleted.connect(lambda: self.remove_card(card))
        card.card_clicked.connect(self._on_card_clicked)
        card.selection_changed.connect(self.update_selection_counter)

        # Monotonic sorting key: reverse order requires bisecting negated keys
        sort_key = extract_chronological_key(item_data)
        neg_key = -sort_key

        # Binary search insertion index: O(log N)
        target_idx = bisect.bisect_right(self._card_sort_keys, neg_key)

        self._card_sort_keys.insert(target_idx, neg_key)
        self.cards.insert(target_idx, card)
        if dedup_key:
            self._card_id_set.add(dedup_key)

        # Suspend widget repaint during layout re-indexing
        self.scroll_widget.setUpdatesEnabled(False)
        try:
            self.media_grid_layout.insertWidget(target_idx, card)
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

        self.update_selection_counter()
        self._update_action_button_states()

    def _on_card_clicked(
        self, card: MediaCard, modifiers: Optional[Qt.KeyboardModifier] = None
    ) -> None:
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier

        cards = self.get_all_media_cards()
        if not cards or card not in cards:
            return

        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            last_card = getattr(self, "_last_selected_card", None)
            if last_card and last_card in cards:
                idx1 = cards.index(last_card)
                idx2 = cards.index(card)
                start, end = min(idx1, idx2), max(idx1, idx2)
                target_state = not card.is_selected
                for i in range(start, end + 1):
                    cards[i].set_selected(target_state)
            else:
                card.toggle_selected()
        else:
            card.toggle_selected()

        self._last_selected_card = card
        self.update_selection_counter()

    def toggle_select_all(self) -> None:
        cards = self.get_all_media_cards()
        if not cards:
            return

        all_selected = all(card.is_selected for card in cards)
        new_state = not all_selected

        for card in cards:
            card.set_selected(new_state)

        self.update_selection_counter()

    def smooth_scroll_queue_to_bottom(self) -> None:
        v_bar = self.scroll_area.verticalScrollBar()
        if not v_bar:
            return
        target_val = v_bar.maximum()
        self._queue_scroll_anim = QPropertyAnimation(v_bar, b"value", self)
        self._queue_scroll_anim.setDuration(350)
        self._queue_scroll_anim.setStartValue(v_bar.value())
        self._queue_scroll_anim.setEndValue(target_val)
        self._queue_scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._queue_scroll_anim.start()

    def remove_card(self, card: MediaCard) -> None:
        if card in self.cards:
            idx = self.cards.index(card)
            self.cards.pop(idx)
            if idx < len(self._card_sort_keys):
                self._card_sort_keys.pop(idx)

            ed = card.get_item_data()
            cid = str(ed.get("id") or ed.get("shortcode") or ed.get("url") or "")
            c_idx = str(ed.get("carousel_index", ""))
            dedup_key = f"{cid}::{c_idx}" if cid else ""
            self._card_id_set.discard(dedup_key)

        self.media_grid_layout.removeWidget(card)
        card.cleanup()
        card.setParent(None)
        card.deleteLater()
        self.update_selection_counter()
        self._update_action_button_states()

    def on_inspection_finished(self, count: int) -> None:
        self.progress_bar.setValue(100)
        self._set_button_icon(self.btn_inspect, "search", "#FFFFFF", 18)
        self.btn_inspect.setToolTip(self.tr_text("inspect_media"))
        self.lbl_status.setText(
            self.tr_text("status_inspection_done", count=len(self.cards))
        )
        self.lbl_toast.setText("")
        logger.info(
            f"Inspection complete: {count} new item(s) found. Total cards: {len(self.cards)}"
        )
        self._update_action_button_states()

    def start_download(self) -> None:
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self._set_button_icon(self.btn_download_all, "download", "#FFFFFF", 18)
            self.lbl_status.setText(self.tr_text("status_download_cancelled"))
            self.show_toast(self.tr_text("toast_download_cancelled"))
            self._update_action_button_states()
            return

        selected_cards = [
            c
            for c in self.cards
            if c.is_selected
            and getattr(c, "status", "").lower() != "finished"
            and not getattr(c, "is_finished", False)
        ]

        if not selected_cards:
            already_done = any(
                c.is_selected
                and (
                    getattr(c, "status", "").lower() == "finished"
                    or getattr(c, "is_finished", False)
                )
                for c in self.cards
            )
            if already_done:
                self.show_toast("Selected items are already downloaded.")
            else:
                self.show_toast(self.tr_text("toast_no_selection"), is_error=True)
            return

        selected_cards.sort(
            key=lambda card_obj: extract_chronological_key(card_obj.get_item_data()),
            reverse=True,
        )

        items = [c.get_item_data() for c in selected_cards]
        for card in selected_cards:
            card.set_status("queued")

        self._set_button_icon(self.btn_download_all, "stop", "#FFFFFF", 18)
        self.lbl_status.setText(self.tr_text("status_downloading", count=len(items)))

        self.download_worker = DownloadWorker(
            items=items,
            save_folder=self.save_folder,
            cookie_file=self.cookie_manager.get_cookie_file_path(),
            cookie_str=self.cookie_manager.get_cookie_string(),
            parent=self,
        )
        self.download_worker.progress.connect(self.progress_bar.setValue)
        self.download_worker.item_started.connect(self._on_item_started)
        self.download_worker.item_finished.connect(self._on_item_finished)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()
        self._update_action_button_states()

    def _on_item_started(self, item_id: str) -> None:
        for card in self.cards:
            cid = str(
                getattr(card, "item_id", None)
                or card.item_data.get("id")
                or card.item_data.get("shortcode")
            )
            if cid == str(item_id):
                card.set_status("downloading")

    def _on_item_finished(self, item_id: str, ok: bool) -> None:
        for card in self.cards:
            cid = str(
                getattr(card, "item_id", None)
                or card.item_data.get("id")
                or card.item_data.get("shortcode")
            )
            if cid == str(item_id):
                card.set_status("finished" if ok else "error")
                if ok:
                    card.set_selected(False)
        self.update_selection_counter()

    def _on_download_finished(self, success_count: int) -> None:
        self._set_button_icon(self.btn_download_all, "download", "#FFFFFF", 18)
        self.progress_bar.setValue(100)

        for card in self.cards:
            if getattr(card, "status", "") in ("queued", "downloading"):
                card.set_status("ready")

        if self.download_worker and self.download_worker._is_cancelled:
            self.lbl_status.setText(self.tr_text("status_download_cancelled"))
            self.show_toast(self.tr_text("toast_download_cancelled"))
        else:
            self.lbl_status.setText(
                self.tr_text("status_download_done", count=success_count)
            )
            self.show_toast(self.tr_text("status_download_done", count=success_count))

        self._update_action_button_states()

    def clear_all_cards(self) -> None:
        if not self.cards:
            return

        self.scroll_widget.setUpdatesEnabled(False)
        try:
            for card in list(self.cards):
                self.media_grid_layout.removeWidget(card)
                card.cleanup()
                card.setParent(None)
                card.deleteLater()
            self.cards.clear()
            self._card_id_set.clear()
            self._card_sort_keys.clear()
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

        self.update_selection_counter()
        self._update_action_button_states()

    def update_selection_counter(self) -> None:
        cards = self.get_all_media_cards()
        total = len(cards)
        selected = sum(1 for c in cards if c.is_selected)

        self.lbl_queue_count.setText(f"Media Queue ({selected}/{total} Selected)")
        self.tab_widget.setTabText(0, f"Media Queue ({selected}/{total})")

        if total > 0 and selected == total:
            self.btn_select_all.setToolTip(
                self.tr_text("btn_deselect_all")
                if "btn_deselect_all" in TRANSLATIONS.get(self.current_lang, {})
                else "Deselect All"
            )
        else:
            self.btn_select_all.setToolTip(self.tr_text("btn_select_all"))

        self._update_action_button_states()

    def select_all_cards(self) -> None:
        for card in self.get_all_media_cards():
            card.set_selected(True)
        self.update_selection_counter()

    def deselect_all_cards(self) -> None:
        for card in self.get_all_media_cards():
            card.set_selected(False)
        self.update_selection_counter()

    def invert_selection(self) -> None:
        for card in self.cards:
            card.set_selected(not card.is_selected)
        self.update_selection_counter()

    def toggle_active_selection(self) -> None:
        if 0 <= self._last_selected_index < len(self.cards):
            card = self.cards[self._last_selected_index]
            card.set_selected(not card.is_selected)
            self.update_selection_counter()

    def delete_selected_cards(self) -> None:
        """Batches deletion of selected cards, synchronizing dedup sets and sort keys."""
        selected_cards = [c for c in self.cards if c.is_selected]
        if not selected_cards:
            self.show_toast(self.tr_text("toast_no_selection"), is_error=True)
            return

        self.scroll_widget.setUpdatesEnabled(False)
        try:
            for card in selected_cards:
                self.remove_card(card)
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

        self.update_selection_counter()
        self._update_action_button_states()

    def update_cookie_status(self) -> None:
        if self.cookie_manager.has_cookies():
            user_id = self.cookie_manager.get_user_id()
            self.lbl_cookie_status.setText(
                self.tr_text("cookie_connected", user=user_id or "Active")
            )
            self.lbl_cookie_status.setStyleSheet("color: #10B981;")
        else:
            self.lbl_cookie_status.setText(self.tr_text("cookie_disconnected"))
            self.lbl_cookie_status.setStyleSheet("color: #A0A0B2;")

    def clear_completed_cards(self) -> None:
        """Batches removal of finished downloads with atomic layout and registry sync."""
        completed_cards = [
            c
            for c in self.cards
            if getattr(c, "status", "").lower() == "finished"
            or getattr(c, "is_finished", False)
        ]
        if not completed_cards:
            self.show_toast(
                self.tr_text("toast_no_completed")
                if "toast_no_completed" in TRANSLATIONS.get(self.current_lang, {})
                else "No completed items to clear."
            )
            return

        self.scroll_widget.setUpdatesEnabled(False)
        try:
            for card in completed_cards:
                self.remove_card(card)
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

        self.update_selection_counter()
        self._update_action_button_states()

    def import_cookie(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr_text("dialog_import_cookie"),
            "",
            "Cookie (*.txt *.json);;All (*.*)",
        )
        if file_path and self.cookie_manager.import_cookie_file(file_path):
            self.update_cookie_status()
            self.show_toast(self.tr_text("toast_cookie_success"))
        else:
            self.show_toast(self.tr_text("toast_cookie_failed"), is_error=True)

    def browse_save_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, self.tr_text("dialog_select_folder"), self.save_folder
        )
        if folder:
            self.save_folder = os.path.abspath(folder)
            self.save_settings()
            self.show_toast(f"Save folder: {self.save_folder}")

    def open_save_folder(self) -> None:
        if os.path.exists(self.save_folder):
            if sys.platform == "win32":
                os.startfile(self.save_folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.save_folder])
            else:
                subprocess.Popen(["xdg-open", self.save_folder])

    def show_toast(self, msg: str, is_error: bool = False) -> None:
        self.lbl_toast.setStyleSheet(f"color: {'#FF6B6B' if is_error else '#10B981'};")
        self.lbl_toast.setText(msg)
        QTimer.singleShot(3500, lambda: self.lbl_toast.setText(""))

    def _on_lang_changed(self, idx: int) -> None:
        self.current_lang = "th" if idx == 1 else "en"
        self.apply_translations()
        self.save_settings()

    def _resolve_settings_file(self) -> str:
        """Resolves configuration file in user AppData space."""
        from utils.file_utils import get_user_data_dir

        return os.path.join(get_user_data_dir(), "settings.json")

    def load_settings(self) -> None:
        settings_path = self._resolve_settings_file()
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.save_folder = d.get("save_folder", self.save_folder)
                    self.current_lang = d.get("language", self.current_lang)
                    self.auto_clipboard = bool(
                        d.get("auto_clipboard", self.auto_clipboard)
                    )
                    self.profile_mode = d.get("profile_mode", self.profile_mode)
                    self.quality_preset = d.get("quality_preset", self.quality_preset)
                    self._saved_geometry_hex = d.get("window_geometry", "")
                    self._is_maximized = bool(d.get("window_maximized", False))
            except Exception as e:
                logger.debug("Failed to load settings: %s", e)

    def save_settings(self) -> None:
        settings_path = self._resolve_settings_file()
        try:
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            geometry_hex = self.saveGeometry().toHex().data().decode("utf-8")
            payload = {
                "save_folder": self.save_folder,
                "language": self.current_lang,
                "auto_clipboard": self.auto_clipboard,
                "profile_mode": self.profile_mode,
                "quality_preset": self.quality_preset,
                "window_geometry": geometry_hex,
                "window_maximized": self.isMaximized(),
            }
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.debug("Failed to save settings: %s", e)

    def closeEvent(self, event) -> None:
        self.save_settings()
        super().closeEvent(event)

    def _update_action_button_states(self) -> None:
        is_inspecting = bool(self.inspect_worker and self.inspect_worker.isRunning())
        is_downloading = bool(self.download_worker and self.download_worker.isRunning())

        cards = self.get_all_media_cards()
        total_count = len(cards)
        selected_count = sum(1 for c in cards if c.is_selected)
        completed_count = sum(
            1
            for c in cards
            if getattr(c, "status", "").lower() == "finished"
            or getattr(c, "is_finished", False)
        )

        if is_downloading:
            self.btn_inspect.setEnabled(False)
        else:
            self.btn_inspect.setEnabled(True)

        if is_inspecting:
            self._set_button_icon(self.btn_inspect, "stop", "#FFFFFF", 18)
            self.btn_inspect.setToolTip(self.tr_text("inspect_cancel"))
        else:
            self._set_button_icon(self.btn_inspect, "search", "#FFFFFF", 18)
            self.btn_inspect.setToolTip(self.tr_text("inspect_media"))

        if is_downloading:
            self.btn_download_all.setEnabled(True)
            self._set_button_icon(self.btn_download_all, "stop", "#FFFFFF", 18)
            self.btn_download_all.setToolTip(self.tr_text("btn_cancel_download"))
        elif is_inspecting or selected_count == 0:
            self.btn_download_all.setEnabled(False)
            self._set_button_icon(self.btn_download_all, "download", "#FFFFFF", 18)
            self.btn_download_all.setToolTip(self.tr_text("btn_download_selected"))
        else:
            self.btn_download_all.setEnabled(True)
            self._set_button_icon(self.btn_download_all, "download", "#FFFFFF", 18)
            self.btn_download_all.setToolTip(self.tr_text("btn_download_selected"))

        if hasattr(self, "btn_select_all"):
            self.btn_select_all.setEnabled(total_count > 0 and not is_downloading)

        if hasattr(self, "btn_delete_selected"):
            self.btn_delete_selected.setEnabled(
                selected_count > 0 and not is_downloading
            )

        if hasattr(self, "btn_clear_completed"):
            self.btn_clear_completed.setEnabled(
                completed_count > 0 and not is_downloading
            )

        if hasattr(self, "btn_import_cookie"):
            self.btn_import_cookie.setEnabled(not is_inspecting and not is_downloading)

        if hasattr(self, "btn_change_folder"):
            self.btn_change_folder.setEnabled(not is_downloading)

        if hasattr(self, "combo_profile_mode"):
            self.combo_profile_mode.setEnabled(not is_inspecting and not is_downloading)

        if hasattr(self, "combo_batch_limit"):
            self.combo_batch_limit.setEnabled(not is_inspecting and not is_downloading)

        if hasattr(self, "url_container"):
            url_count = self.url_container.count()
            self.url_container.btn_clear_all.setEnabled(
                url_count > 0 and not is_inspecting
            )
            input_has_text = bool(self.url_container.input_edit.text().strip())
            self.url_container.btn_add.setEnabled(input_has_text and not is_inspecting)
