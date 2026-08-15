"""
gui/main_window.py - Main Window containing all controls, event listeners, and worker orchestration.
"""

import os
import re
import subprocess
import sys
from PyQt6.QtCore import QLocale, QSettings, Qt
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config.constants import (
    APPLICATION_NAME,
    INSTAGRAM_URL_REGEX,
    ORGANIZATION_NAME,
)
from config.translations import TRANSLATIONS
from core.cookie_manager import sanitize_and_save_instagram_cookies
from core.download_worker import GridDownloadWorker
from core.inspect_worker import InspectionWorker
from core.parser import parse_instagram_url
from gui.styles import DARK_THEME_QSS
from gui.widgets.media_card import MediaCardWidget
from utils.file_utils import get_ffmpeg_dir, get_icon_path


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(820, 760)

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
        default_dl = os.path.join(os.path.expanduser("~"), "Downloads", "InstagramDownloads")
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
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(10)

        # Header Row
        header_layout = QHBoxLayout()
        self.title_label = QLabel("Instagram Pro Downloader - Studio Inspector")
        self.title_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header_layout.addWidget(self.title_label)

        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem("🇹🇭 ภาษาไทย", "th")
        self.cmb_lang.addItem("🇺🇸 English", "en")
        self.cmb_lang.setFixedHeight(26)
        idx = self.cmb_lang.findData(self.current_lang)
        if idx >= 0:
            self.cmb_lang.setCurrentIndex(idx)
        self.cmb_lang.currentIndexChanged.connect(self.on_language_changed)
        header_layout.addWidget(self.cmb_lang, alignment=Qt.AlignmentFlag.AlignRight)
        main_layout.addLayout(header_layout)

        # URLs Input Group
        self.input_group = QGroupBox()
        input_layout = QVBoxLayout(self.input_group)
        input_layout.setSpacing(6)

        self.txt_urls = QPlainTextEdit()
        self.txt_urls.setFixedHeight(70)
        input_layout.addWidget(self.txt_urls)

        self.chk_clipboard = QCheckBox()
        input_layout.addWidget(self.chk_clipboard)

        btn_row = QHBoxLayout()
        self.btn_inspect = QPushButton()
        self.btn_inspect.setFixedHeight(30)
        self.btn_inspect.setStyleSheet("background-color: #007acc;")
        self.btn_inspect.clicked.connect(self.start_inspection)
        btn_row.addWidget(self.btn_inspect, stretch=3)

        self.btn_clear_input = QPushButton()
        self.btn_clear_input.setFixedHeight(30)
        self.btn_clear_input.setStyleSheet("background-color: #4a4a5a;")
        self.btn_clear_input.clicked.connect(lambda: self.txt_urls.clear())
        btn_row.addWidget(self.btn_clear_input, stretch=1)

        input_layout.addLayout(btn_row)
        main_layout.addWidget(self.input_group)

        # Grid Inspector Group
        self.grid_group = QGroupBox()
        grid_group_layout = QVBoxLayout(self.grid_group)
        grid_group_layout.setContentsMargins(8, 10, 8, 8)
        grid_group_layout.setSpacing(6)

        sel_toolbar = QHBoxLayout()
        self.btn_select_all = QPushButton()
        self.btn_select_all.setFixedHeight(24)
        self.btn_select_all.setStyleSheet("background-color: #2e2e3d; font-size: 11px; padding: 2px 10px;")
        self.btn_select_all.clicked.connect(self.select_all_cards)
        sel_toolbar.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton()
        self.btn_delete_selected.setFixedHeight(24)
        self.btn_delete_selected.setStyleSheet("background-color: #8b2635; font-size: 11px; padding: 2px 10px;")
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        sel_toolbar.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton()
        self.btn_clear_completed.setFixedHeight(24)
        self.btn_clear_completed.setStyleSheet("background-color: #3b3b4f; font-size: 11px; padding: 2px 10px;")
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        sel_toolbar.addWidget(self.btn_clear_completed)

        sel_toolbar.addStretch()
        self.lbl_selection_count = QLabel("เลือกอยู่: 0 / 0 รายการ")
        self.lbl_selection_count.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        sel_toolbar.addWidget(self.lbl_selection_count)
        grid_group_layout.addLayout(sel_toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: #17171e; border: none; border-radius: 6px;")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(6, 6, 6, 6)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        grid_group_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.grid_group, stretch=1)

        # Settings Group (Save Path & Cookie Management)
        self.settings_group = QGroupBox()
        settings_layout = QVBoxLayout(self.settings_group)
        settings_layout.setSpacing(8)

        # Row 1: Folder Selection
        path_row = QHBoxLayout()
        self.lbl_path = QLabel()
        self.lbl_path.setStyleSheet("color: #cccccc; font-size: 11px;")
        path_row.addWidget(self.lbl_path, stretch=1)

        self.btn_browse = QPushButton()
        self.btn_browse.setFixedHeight(26)
        self.btn_browse.clicked.connect(self.browse_folder)
        path_row.addWidget(self.btn_browse)

        self.btn_open = QPushButton()
        self.btn_open.setFixedHeight(26)
        self.btn_open.clicked.connect(self.open_folder)
        path_row.addWidget(self.btn_open)
        settings_layout.addLayout(path_row)

        # Row 2: Cookie Controls
        cookie_row = QHBoxLayout()
        self.lbl_cookie_status = QLabel()
        self.lbl_cookie_status.setStyleSheet("font-size: 11px;")
        cookie_row.addWidget(self.lbl_cookie_status, stretch=1)

        self.btn_import_cookie = QPushButton()
        self.btn_import_cookie.setFixedHeight(26)
        self.btn_import_cookie.clicked.connect(self.import_cookie_file)
        cookie_row.addWidget(self.btn_import_cookie)

        self.btn_clear_cookie = QPushButton()
        self.btn_clear_cookie.setFixedHeight(26)
        self.btn_clear_cookie.setStyleSheet("background-color: #8b2635;")
        self.btn_clear_cookie.clicked.connect(self.clear_cookie_file)
        cookie_row.addWidget(self.btn_clear_cookie)
        settings_layout.addLayout(cookie_row)

        main_layout.addWidget(self.settings_group)

        # Action Buttons & Progress
        btn_action_layout = QHBoxLayout()
        self.btn_download_all = QPushButton()
        self.btn_download_all.setFixedHeight(36)
        self.btn_download_all.clicked.connect(self.start_download_all)
        btn_action_layout.addWidget(self.btn_download_all, stretch=3)

        self.btn_cancel = QPushButton()
        self.btn_cancel.setFixedHeight(36)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_operation)
        btn_action_layout.addWidget(self.btn_cancel, stretch=1)
        main_layout.addLayout(btn_action_layout)

        # Status Bar & Footer
        self.lbl_status = QLabel()
        self.lbl_status.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        main_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setValue(0)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.progress_bar)

        footer_layout = QHBoxLayout()
        self.lbl_footer = QLabel()
        self.lbl_footer.setStyleSheet("color: #888888; font-size: 11px;")
        footer_layout.addWidget(self.lbl_footer)
        main_layout.addLayout(footer_layout)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.SelectAll) or (
            event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if not self.txt_urls.hasFocus():
                self.select_all_cards()
                return

        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self.txt_urls.hasFocus():
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

        elif modifiers & Qt.KeyboardModifier.ShiftModifier and self.anchor_card_idx is not None:
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
            self.t("lbl_selection_format").format(selected=selected_count, total=total_count)
        )

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
                    QMessageBox.information(self, "Success", "Cookie deleted successfully.")
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
        self.txt_urls.setPlaceholderText(self.t("url_placeholder"))
        self.chk_clipboard.setText(self.t("clipboard_chk"))
        self.chk_clipboard.setToolTip(self.t("clipboard_tooltip"))
        self.btn_inspect.setText(self.t("btn_inspect"))
        self.btn_clear_input.setText(self.t("btn_clear_input"))
        self.grid_group.setTitle(self.t("grid_group"))
        self.btn_select_all.setText(self.t("btn_select_all"))
        self.btn_delete_selected.setText(self.t("btn_delete_selected"))
        self.btn_clear_completed.setText(self.t("btn_clear_completed"))
        self.settings_group.setTitle(self.t("settings_group"))
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

    def apply_dark_theme(self) -> None:
        self.setStyleSheet(DARK_THEME_QSS)

    def load_saved_settings(self) -> None:
        self.chk_clipboard.setChecked(self.settings.value("auto_clipboard", True, type=bool))

    def setup_clipboard_monitor(self) -> None:
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_change)

    def on_clipboard_change(self) -> None:
        if not self.chk_clipboard.isChecked():
            return

        text = self.clipboard.text().strip()
        if not text or text == self.last_clipboard_text:
            return

        matches = re.findall(INSTAGRAM_URL_REGEX, text)
        if matches:
            self.last_clipboard_text = text
            current_text = self.txt_urls.toPlainText().strip()
            existing_urls = set(current_text.splitlines()) if current_text else set()

            new_urls = []
            for u in matches:
                clean_u = u.rstrip(".,;)]}>\"'?")
                if clean_u not in existing_urls:
                    new_urls.append(clean_u)
                    existing_urls.add(clean_u)

            if new_urls:
                if current_text:
                    self.txt_urls.appendPlainText("\n".join(new_urls))
                else:
                    self.txt_urls.setPlainText("\n".join(new_urls))
                self.lbl_footer.setText(self.t("footer_clip_detected"))
                self.lbl_footer.setStyleSheet("color: #28a745; font-size: 11px;")

    def start_inspection(self) -> None:
        raw_text = self.txt_urls.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Warning", self.t("warn_no_url"))
            return

        raw_matches = re.findall(INSTAGRAM_URL_REGEX, raw_text)
        valid_urls = []
        has_story = False

        for item in raw_matches:
            parsed = parse_instagram_url(item)
            if parsed:
                valid_urls.append(parsed["clean_url"])
                if parsed["type"] in ("story", "highlight"):
                    has_story = True

        valid_urls = list(dict.fromkeys(valid_urls))
        if not valid_urls:
            QMessageBox.warning(
                self,
                "Warning",
                "No valid Instagram URLs (Post, Reel, Carousel, Story) found.",
            )
            return

        if has_story and not os.path.exists(self.cookie_path):
            QMessageBox.information(self, "Cookie Required", self.t("warn_story_need_cookie"))

        self.btn_inspect.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText(self.t("status_inspecting"))

        self.inspect_worker = InspectionWorker(valid_urls, self.cookie_path)
        self.inspect_worker.item_inspected.connect(self.add_card)
        self.inspect_worker.progress_status.connect(lambda msg: self.lbl_status.setText(msg))
        self.inspect_worker.finished_inspection.connect(self.on_inspection_finished)
        self.inspect_worker.start()

    def add_card(self, data: dict) -> None:
        card = MediaCardWidget(data, self.current_lang)
        card.clicked.connect(self.on_card_clicked)
        card.removed.connect(self.remove_card)
        self.cards.append(card)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.update_selection_ui()

    def remove_card(self, card: MediaCardWidget) -> None:
        if card in self.cards:
            if card.thumb_loader and card.thumb_loader.isRunning():
                card.thumb_loader.cancel()
                card.thumb_loader.wait(300)
            self.cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
            self.update_selection_ui()

    def on_inspection_finished(self, count: int) -> None:
        self.btn_inspect.setEnabled(True)
        self.btn_download_all.setEnabled(len(self.cards) > 0)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText(self.t("inspect_done").format(count=len(self.cards)))
        self.txt_urls.clear()
        self.update_selection_ui()

    def start_download_all(self) -> None:
        if not self.cards:
            return

        has_audio = any(card.get_selected_format() == "audio_mp3" for card in self.cards)
        if has_audio and not get_ffmpeg_dir():
            QMessageBox.warning(self, "FFmpeg Required", self.t("warn_need_ffmpeg"))
            return

        download_list = []
        for card in self.cards:
            item_copy = dict(card.data)
            item_copy["selected_format"] = card.get_selected_format()
            download_list.append(item_copy)
            card.lbl_status.setText("Queued...")
            card.lbl_status.setStyleSheet("color: #e6b800; font-size: 11px;")

        self.btn_inspect.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText(self.t("status_downloading"))

        self.download_worker = GridDownloadWorker(download_list, self.save_dir, self.cookie_path)
        self.download_worker.item_started.connect(self.on_item_download_started)
        self.download_worker.item_finished.connect(self.on_item_download_finished)
        self.download_worker.progress_signal.connect(self.update_progress)
        self.download_worker.all_finished.connect(self.on_all_downloads_finished)
        self.download_worker.start()

    def on_item_download_started(self, card_idx: int, total: int, shortcode: str) -> None:
        if card_idx < len(self.cards):
            self.cards[card_idx].lbl_status.setText("Downloading...")
            self.cards[card_idx].lbl_status.setStyleSheet("color: #fa7e1e; font-size: 11px;")
        self.lbl_status.setText(f"Downloading [{card_idx+1}/{total}]: {shortcode}")

    def on_item_download_finished(self, card_idx: int, ok: bool, text: str, saved_path: str) -> None:
        if card_idx < len(self.cards):
            if ok:
                self.cards[card_idx].mark_completed(saved_path)
            else:
                self.cards[card_idx].lbl_status.setText("✖ Failed")
                self.cards[card_idx].lbl_status.setStyleSheet("color: #dc3545; font-size: 11px;")

    def update_progress(self, d: dict) -> None:
        self.progress_bar.setValue(int(d["percent"]))
        dl_mb = d["downloaded"] / (1024 * 1024)
        total_mb = d["total"] / (1024 * 1024) if d["total"] else 0
        speed = d["speed"]
        speed_str = f"{speed / (1024 * 1024):.2f} MB/s" if speed > 1024 * 1024 else f"{speed / 1024:.1f} KB/s"
        if total_mb > 0:
            self.lbl_status.setText(f"Downloading... {dl_mb:.2f} / {total_mb:.2f} MB ({speed_str})")

    def on_all_downloads_finished(self, success: int, fail: int, is_cancelled: bool) -> None:
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