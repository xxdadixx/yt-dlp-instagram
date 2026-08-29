from __future__ import annotations

import io
import urllib.request
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QThread,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QFont, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SwipeableAnimatedViewport(QFrame):
    """
    High-Performance Animated Viewport.
    Supports touch-like mouse swipe/drag gestures and smooth OutCubic sliding transitions.
    """

    swipe_left = pyqtSignal()
    swipe_right = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 320)
        self.setStyleSheet(
            "background-color: #16161F; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.08);"
        )
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self.label_curr = QLabel(self)
        self.label_curr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_curr.setStyleSheet("background: transparent;")

        self.label_next = QLabel(self)
        self.label_next.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_next.setStyleSheet("background: transparent;")
        self.label_next.hide()

        self._drag_start_pos: Optional[QPointF] = None
        self._is_dragging: bool = False
        self._swipe_threshold: int = 50
        self._current_pixmap: Optional[QPixmap] = None
        self._anim_group: Optional[QParallelAnimationGroup] = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (
            not self._anim_group
            or self._anim_group.state() != QParallelAnimationGroup.State.Running
        ):
            self.label_curr.setGeometry(0, 0, self.width(), self.height())
            self.label_next.setGeometry(0, 0, self.width(), self.height())
            if self._current_pixmap and not self._current_pixmap.isNull():
                self.label_curr.setPixmap(self._scale_pixmap(self._current_pixmap))

    def set_image_direct(
        self, pixmap: Optional[QPixmap], placeholder: str = ""
    ) -> None:
        """ตั้งค่าภาพโดยตรงและปรับสเกลให้พอดีกับ Viewport"""
        if (
            self._anim_group
            and self._anim_group.state() == QParallelAnimationGroup.State.Running
        ):
            self._anim_group.stop()

        self._current_pixmap = pixmap if (pixmap and not pixmap.isNull()) else None
        self.label_curr.setGeometry(
            0, 0, max(self.width(), 100), max(self.height(), 100)
        )
        self.label_next.setGeometry(
            0, 0, max(self.width(), 100), max(self.height(), 100)
        )

        if self._current_pixmap:
            self.label_curr.setPixmap(self._scale_pixmap(self._current_pixmap))
            self.label_curr.setText("")
        else:
            self.label_curr.clear()
            self.label_curr.setText(placeholder or "Loading high-resolution image...")
            self.label_curr.setStyleSheet(
                "color: #A0A0B2; font-size: 14px; font-family: 'Segoe UI'; font-weight: 500;"
            )

        self.label_curr.show()
        self.label_next.hide()

    def update_current_pixmap(self, pixmap: QPixmap) -> None:
        """อัปเดตภาพทันทีเมื่อดาวน์โหลดเสร็จสมบูรณ์ในระหว่างที่ผู้ใช้เปิดดูสไลด์นี้อยู่"""
        self._current_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            scaled = self._scale_pixmap(pixmap)
            target_label = (
                self.label_next
                if (
                    self._anim_group
                    and self._anim_group.state()
                    == QParallelAnimationGroup.State.Running
                )
                else self.label_curr
            )
            target_label.setPixmap(scaled)
            target_label.setText("")
            target_label.show()

    def slide_to_image(
        self,
        new_pixmap: Optional[QPixmap],
        direction: str = "next",
        placeholder: str = "",
    ) -> None:
        """เล่น Animation เลื่อนสไลด์ภาพอย่างนุ่มนวล"""
        if (
            self._anim_group
            and self._anim_group.state() == QParallelAnimationGroup.State.Running
        ):
            self._anim_group.stop()
            self.label_curr.setGeometry(0, 0, self.width(), self.height())
            self.label_next.setGeometry(0, 0, self.width(), self.height())

        self._current_pixmap = (
            new_pixmap if (new_pixmap and not new_pixmap.isNull()) else None
        )
        w = max(self.width(), 100)
        h = max(self.height(), 100)

        if self._current_pixmap:
            self.label_next.setPixmap(self._scale_pixmap(self._current_pixmap))
            self.label_next.setText("")
        else:
            self.label_next.clear()
            self.label_next.setText(placeholder or "Loading image...")
            self.label_next.setStyleSheet(
                "color: #A0A0B2; font-size: 14px; font-family: 'Segoe UI'; font-weight: 500;"
            )

        start_curr = QPoint(0, 0)
        end_curr = QPoint(-w if direction == "next" else w, 0)
        start_next = QPoint(w if direction == "next" else -w, 0)
        end_next = QPoint(0, 0)

        self.label_next.setGeometry(start_next.x(), start_next.y(), w, h)
        self.label_next.show()
        self.label_next.raise_()

        self._anim_group = QParallelAnimationGroup(self)

        anim_curr = QPropertyAnimation(self.label_curr, b"pos", self)
        anim_curr.setDuration(240)
        anim_curr.setStartValue(start_curr)
        anim_curr.setEndValue(end_curr)
        anim_curr.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_next = QPropertyAnimation(self.label_next, b"pos", self)
        anim_next.setDuration(240)
        anim_next.setStartValue(start_next)
        anim_next.setEndValue(end_next)
        anim_next.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group.addAnimation(anim_curr)
        self._anim_group.addAnimation(anim_next)

        def on_finished():
            if self._current_pixmap and not self._current_pixmap.isNull():
                self.label_curr.setPixmap(self._scale_pixmap(self._current_pixmap))
                self.label_curr.setText("")
            else:
                self.label_curr.clear()
                self.label_curr.setText(placeholder or "Loading image...")
                self.label_curr.setStyleSheet(
                    "color: #A0A0B2; font-size: 14px; font-family: 'Segoe UI'; font-weight: 500;"
                )

            self.label_curr.setGeometry(0, 0, self.width(), self.height())
            self.label_curr.show()
            self.label_next.hide()

        self._anim_group.finished.connect(on_finished)
        self._anim_group.start()

    def _scale_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if not pixmap or pixmap.isNull():
            return pixmap
        w = max(self.width() - 24, 200)
        h = max(self.height() - 24, 200)
        return pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position()
            self._is_dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

            if self._drag_start_pos is not None:
                delta_x = event.position().x() - self._drag_start_pos.x()
                if delta_x < -self._swipe_threshold:
                    self.swipe_left.emit()
                elif delta_x > self._swipe_threshold:
                    self.swipe_right.emit()

            self._drag_start_pos = None
        super().mouseReleaseEvent(event)


class ImageDownloadWorker(QThread):
    image_loaded = pyqtSignal(int, bytes)

    def __init__(self, index: int, url: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.index = index
        self.url = url

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self.url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                data = response.read()
                if data:
                    self.image_loaded.emit(self.index, data)
        except Exception:
            pass


class ImageViewerDialog(QDialog):
    """
    High-Resolution Photo Gallery Lightbox Dialog with Automatic Preloading.
    """

    def __init__(
        self,
        image_urls: List[str],
        title: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.image_urls = image_urls
        self.post_title = title
        self.current_index = 0
        self.pixmap_cache: Dict[int, QPixmap] = {}
        self.active_workers: List[ImageDownloadWorker] = []

        self.init_ui()
        self._preload_all_images()
        self._load_and_display(is_initial=True)

    def init_ui(self) -> None:
        self.setWindowTitle("Instagram Media Gallery Viewer")
        self.setMinimumSize(840, 680)
        self.resize(920, 760)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #0D0D12;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
            QLabel {
                color: #FFFFFF;
            }
            QPushButton#NavButton {
                background-color: rgba(22, 22, 31, 0.9);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 22px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton#NavButton:hover {
                background-color: #E1306C;
                border-color: #E1306C;
            }
            QPushButton#NavButton:disabled {
                background-color: rgba(22, 22, 31, 0.3);
                color: rgba(255, 255, 255, 0.2);
                border-color: rgba(255, 255, 255, 0.05);
            }
            QPushButton#CloseButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 14px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton#CloseButton:hover {
                background-color: #FF4D4D;
                border-color: #FF4D4D;
            }
            """
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top Bar: Badge Counter + Title + Close Button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(10)

        self.lbl_counter = QLabel(self)
        self.lbl_counter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_counter.setStyleSheet(
            "background-color: #E1306C; color: #FFFFFF; padding: 4px 10px; border-radius: 10px;"
        )
        top_bar.addWidget(self.lbl_counter)

        self.lbl_title = QLabel(self.post_title or "Post Photo Gallery", self)
        self.lbl_title.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self.lbl_title.setStyleSheet("color: #E0E0E6;")
        top_bar.addWidget(self.lbl_title, 1)

        self.btn_close = QPushButton("✕", self)
        self.btn_close.setObjectName("CloseButton")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        top_bar.addWidget(self.btn_close)

        main_layout.addLayout(top_bar)

        # 2. Main Large Image View Area
        view_area = QHBoxLayout()
        view_area.setContentsMargins(0, 0, 0, 0)
        view_area.setSpacing(10)

        self.btn_prev = QPushButton("❮", self)
        self.btn_prev.setObjectName("NavButton")
        self.btn_prev.setFixedSize(44, 44)
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.clicked.connect(self.prev_image)
        view_area.addWidget(self.btn_prev, 0, Qt.AlignmentFlag.AlignVCenter)

        self.viewport = SwipeableAnimatedViewport(self)
        self.viewport.swipe_left.connect(self.next_image)
        self.viewport.swipe_right.connect(self.prev_image)
        view_area.addWidget(self.viewport, 1)

        self.btn_next = QPushButton("❯", self)
        self.btn_next.setObjectName("NavButton")
        self.btn_next.setFixedSize(44, 44)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.clicked.connect(self.next_image)
        view_area.addWidget(self.btn_next, 0, Qt.AlignmentFlag.AlignVCenter)

        main_layout.addLayout(view_area, 1)
        self.update_nav_controls()

    def update_nav_controls(self) -> None:
        total = len(self.image_urls)
        self.lbl_counter.setText(f"{self.current_index + 1} / {total}")
        has_multiple = total > 1
        self.btn_prev.setVisible(has_multiple)
        self.btn_next.setVisible(has_multiple)
        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < total - 1)

    def _preload_all_images(self) -> None:
        """ดาวน์โหลดรูปภาพทั้งหมดในโพสต์ล่วงหน้าแบบ Asynchronous"""
        for idx, url in enumerate(self.image_urls):
            if idx not in self.pixmap_cache and url.startswith("http"):
                worker = ImageDownloadWorker(idx, url, self)
                worker.image_loaded.connect(self._on_image_downloaded)
                self.active_workers.append(worker)
                worker.start()

    def _on_image_downloaded(self, index: int, data: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            self.pixmap_cache[index] = pixmap
            if index == self.current_index:
                self.viewport.update_current_pixmap(pixmap)

    def _load_and_display(
        self, is_initial: bool = False, direction: str = "next"
    ) -> None:
        if not self.image_urls:
            self.viewport.set_image_direct(None, placeholder="No images in post.")
            return

        if self.current_index in self.pixmap_cache:
            cached_pix = self.pixmap_cache[self.current_index]
            if is_initial:
                self.viewport.set_image_direct(cached_pix)
            else:
                self.viewport.slide_to_image(cached_pix, direction=direction)
            return

        if is_initial:
            self.viewport.set_image_direct(
                None, placeholder="Loading high-resolution image..."
            )
        else:
            self.viewport.slide_to_image(
                None, direction=direction, placeholder="Loading image..."
            )

    def prev_image(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self.update_nav_controls()
            self._load_and_display(is_initial=False, direction="prev")

    def next_image(self) -> None:
        if self.current_index < len(self.image_urls) - 1:
            self.current_index += 1
            self.update_nav_controls()
            self._load_and_display(is_initial=False, direction="next")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.current_index in self.pixmap_cache:
            self.viewport.set_image_direct(self.pixmap_cache[self.current_index])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.current_index in self.pixmap_cache:
            self.viewport.set_image_direct(self.pixmap_cache[self.current_index])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.prev_image()
        elif event.key() == Qt.Key.Key_Right:
            self.next_image()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
