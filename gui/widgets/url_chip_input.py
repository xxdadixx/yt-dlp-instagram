"""
gui/widgets/url_chip_input.py - URL Chip Deck with dual Grid/List presentation modes,
author username extraction, and resilient asynchronous CDN thumbnail previewing.
"""

from __future__ import annotations

import html as html_lib
import importlib
import json
import logging
import re
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    QThreadPool,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.parser import extract_instagram_urls, parse_instagram_url, shortcode_to_id
from gui.icons import get_icon
from gui.widgets.media_card import ThumbnailHoverPopup
from gui.widgets.thumbnail_loader import GLOBAL_THUMB_CACHE

try:
    cffi_requests: Any = importlib.import_module("curl_cffi.requests")
except Exception:
    cffi_requests = None

logger = logging.getLogger(__name__)

# Shared memory cache for extracted author usernames
GLOBAL_URL_USER_CACHE: Dict[str, str] = {}


class PreviewSignals(QObject):
    loaded = pyqtSignal(str, bytes, str)


def _find_user_dict_recursive(obj: Any, target_user: str) -> Optional[dict[str, Any]]:
    """Recursively searches a JSON structure for a user object specifically matching target_user."""
    target = target_user.lower().strip().lstrip("@")
    if isinstance(obj, dict):
        uname = str(obj.get("username") or "").lower().strip().lstrip("@")
        if uname == target:
            if any(
                k in obj
                for k in (
                    "profile_pic_url_hd",
                    "profile_pic_url",
                    "hd_profile_pic_url_info",
                )
            ):
                return obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found = _find_user_dict_recursive(v, target)
                if found:
                    return found
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                found = _find_user_dict_recursive(item, target)
                if found:
                    return found
    return None


class URLPreviewTask(QRunnable):
    """Background task to fetch media thumbnail preview and author handle using

    TLS impersonation, authenticated API resolution, and resilient embed/profile markup extraction.
    """

    def __init__(
        self,
        raw_url: str,
        shortcode: Optional[str],
        cookie_str: str,
        signals: PreviewSignals,
        username: Optional[str] = None,
        target_type: str = "MEDIA",
    ) -> None:
        super().__init__()
        self.raw_url = raw_url
        self.shortcode = shortcode
        self.username = username
        self.target_type = target_type
        self.cookie_str = cookie_str
        self.signals = signals
        self.setAutoDelete(True)

    def _parse_cookie_dict(self) -> Dict[str, str]:
        """Converts raw semicolon-separated cookie strings into structured dictionaries."""
        cookie_dict: Dict[str, str] = {}
        if self.cookie_str:
            for pair in self.cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    cookie_dict[k.strip()] = v.strip()
        return cookie_dict

    def _download_image_bytes(self, cdn_url: str) -> Optional[bytes]:
        """Streams preview image bytes safely across TLS boundaries."""
        # 1. Primary fast stream via urllib
        try:
            req = urllib.request.Request(
                cdn_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Encoding": "identity",
                    "Connection": "keep-alive",
                },
            )
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                data = resp.read()
                if data:
                    return data
        except Exception as exc:
            logger.debug("urllib CDN stream error: %s", exc)

        # 2. Fallback stream via curl_cffi Chrome impersonation
        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    cdn_url,
                    impersonate="chrome120",
                    timeout=6.0,
                )
                if resp.status_code == 200 and resp.content:
                    return bytes(resp.content)
        except Exception as exc:
            logger.debug("cffi CDN stream error: %s", exc)

        return None

    def _resolve_profile_picture(self, clean_user: str) -> Optional[str]:
        """Resolves the uncompressed HD profile avatar CDN URL strictly for clean_user."""
        target = clean_user.lower().strip().lstrip("@")
        cookie_dict = self._parse_cookie_dict()
        csrf_token = cookie_dict.get("csrftoken", "")

        # -------------------------------------------------------------------------
        # TIER 1: Native Web Profile Info REST API (Identity-Validated)
        # -------------------------------------------------------------------------
        web_api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={target}"
        web_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "X-ASBD-ID": "129477",
            "X-IG-App-ID": "936619743392459",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.instagram.com/{target}/",
            "Origin": "https://www.instagram.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if csrf_token:
            web_headers["X-CSRFToken"] = csrf_token

        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    web_api_url,
                    impersonate="chrome120",
                    headers=web_headers,
                    cookies=cookie_dict,
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    user = _find_user_dict_recursive(data, target)
                    if user:
                        hd_info = user.get("hd_profile_pic_url_info")
                        if isinstance(hd_info, dict) and hd_info.get("url"):
                            return str(hd_info["url"])
                        hd = user.get("profile_pic_url_hd") or user.get(
                            "profile_pic_url"
                        )
                        if hd:
                            return str(hd)
            else:
                if self.cookie_str:
                    web_headers["Cookie"] = self.cookie_str
                req = urllib.request.Request(web_api_url, headers=web_headers)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    user = _find_user_dict_recursive(data, target)
                    if user:
                        hd_info = user.get("hd_profile_pic_url_info")
                        if isinstance(hd_info, dict) and hd_info.get("url"):
                            return str(hd_info["url"])
                        hd = user.get("profile_pic_url_hd") or user.get(
                            "profile_pic_url"
                        )
                        if hd:
                            return str(hd)
        except Exception as exc:
            logger.debug("Tier 1 Web profile info error for @%s: %s", target, exc)

        # -------------------------------------------------------------------------
        # TIER 2: Native Mobile API (i.instagram.com usernameinfo, Identity-Validated)
        # -------------------------------------------------------------------------
        mobile_url = f"https://i.instagram.com/api/v1/users/{target}/usernameinfo/"
        mobile_headers = {
            "User-Agent": (
                "Instagram 315.0.0.38.109 Android (33/13; 420dpi; 1080x2400; "
                "samsung; SM-G991N; o1s; exynos2100; en_US; 564998762)"
            ),
            "X-IG-App-ID": "936619743392459",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    mobile_url,
                    headers=mobile_headers,
                    cookies=cookie_dict,
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    user = _find_user_dict_recursive(data, target)
                    if user:
                        hd_info = user.get("hd_profile_pic_url_info")
                        if isinstance(hd_info, dict) and hd_info.get("url"):
                            return str(hd_info["url"])
                        hd = user.get("profile_pic_url_hd") or user.get(
                            "profile_pic_url"
                        )
                        if hd:
                            return str(hd)
            else:
                if self.cookie_str:
                    mobile_headers["Cookie"] = self.cookie_str
                req = urllib.request.Request(mobile_url, headers=mobile_headers)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    user = _find_user_dict_recursive(data, target)
                    if user:
                        hd_info = user.get("hd_profile_pic_url_info")
                        if isinstance(hd_info, dict) and hd_info.get("url"):
                            return str(hd_info["url"])
                        hd = user.get("profile_pic_url_hd") or user.get(
                            "profile_pic_url"
                        )
                        if hd:
                            return str(hd)
        except Exception as exc:
            logger.debug("Tier 2 Mobile usernameinfo error for @%s: %s", target, exc)

        # -------------------------------------------------------------------------
        # TIER 3: Anonymous Public Page Request (NO COOKIES - Eliminates Viewer Leak)
        # -------------------------------------------------------------------------
        try:
            page_url = f"https://www.instagram.com/{target}/"
            anon_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.instagram.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }

            html_text = ""
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    page_url,
                    impersonate="chrome120",
                    headers=anon_headers,
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    html_text = resp.text
            else:
                req = urllib.request.Request(page_url, headers=anon_headers)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    html_text = resp.read().decode("utf-8", errors="replace")

            if html_text:
                clean_html = html_text.replace(r"\/", "/").replace(r"\u0026", "&")
                clean_html = html_lib.unescape(clean_html)

                # 1. Look for target user's JSON block
                user_patterns = [
                    rf'\{{[^{{}}]*?"username"\s*:\s*"{re.escape(target)}"[^{{}}]*?"profile_pic_url_hd"\s*:\s*"(https://[^"]+)"',
                    rf'\{{[^{{}}]*?"profile_pic_url_hd"\s*:\s*"(https://[^"]+)"[^{{}}]*?"username"\s*:\s*"{re.escape(target)}"',
                    rf'\{{[^{{}}]*?"username"\s*:\s*"{re.escape(target)}"[^{{}}]*?"profile_pic_url"\s*:\s*"(https://[^"]+)"',
                ]
                for pat in user_patterns:
                    m = re.search(pat, clean_html, re.IGNORECASE)
                    if m:
                        cand = m.group(1).replace("&amp;", "&")
                        if cand.startswith("http") and "150x150" not in cand:
                            return cand

                # 2. Parse JSON script nodes
                for script_m in re.finditer(
                    r"<script[^>]*>(.*?)</script>", clean_html, re.DOTALL
                ):
                    content = script_m.group(1).strip()
                    if target in content.lower() and "profile_pic_url" in content:
                        try:
                            if content.startswith(("{", "[")):
                                parsed = json.loads(content)
                                user = _find_user_dict_recursive(parsed, target)
                                if user:
                                    hd = user.get("profile_pic_url_hd") or user.get(
                                        "profile_pic_url"
                                    )
                                    if hd:
                                        return str(hd)
                        except Exception:
                            pass

                # 3. OpenGraph og:image fallback
                m_og = re.search(
                    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
                    clean_html,
                    re.IGNORECASE,
                )
                if m_og:
                    cand = m_og.group(1).replace("&amp;", "&")
                    if cand.startswith("http"):
                        return cand
        except Exception as exc:
            logger.debug("Tier 3 Anonymous SSR avatar error for @%s: %s", target, exc)

        return None

    @pyqtSlot()
    def run(self) -> None:
        cached_bytes = GLOBAL_THUMB_CACHE.get(self.raw_url)
        cached_user = GLOBAL_URL_USER_CACHE.get(self.raw_url, "") or (
            self.username or ""
        )

        # Invalidate low-resolution / thumbnail caches (e.g. 150x150)
        if cached_bytes:
            qimg = QImage()
            if qimg.loadFromData(cached_bytes):
                if qimg.width() >= 300 and qimg.height() >= 300:
                    self.signals.loaded.emit(self.raw_url, cached_bytes, cached_user)
                    return
                else:
                    cached_bytes = None
            else:
                cached_bytes = None

        cdn_url: Optional[str] = None
        detected_username: str = cached_user
        cookie_dict = self._parse_cookie_dict()

        # 1. Profile Target Resolution
        if not self.shortcode and self.username:
            clean_user = self.username.strip().lstrip("@")
            detected_username = clean_user
            cdn_url = self._resolve_profile_picture(clean_user)

        # 2. Post / Reel / Carousel Resolution
        elif self.shortcode:
            if self.cookie_str and "sessionid=" in self.cookie_str:
                media_id = shortcode_to_id(self.shortcode)
                if media_id:
                    api_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
                    headers = {
                        "User-Agent": "Instagram 315.0.0.38.109 Android",
                        "X-IG-App-ID": "936619743392459",
                    }
                    try:
                        if cffi_requests is not None:
                            resp = cffi_requests.get(
                                api_url,
                                headers=headers,
                                cookies=cookie_dict,
                                timeout=5.0,
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                items = data.get("items", [])
                                if items:
                                    first_item = items[0]
                                    u_info = first_item.get("user") or first_item.get(
                                        "owner"
                                    )
                                    if isinstance(u_info, dict) and u_info.get(
                                        "username"
                                    ):
                                        detected_username = str(
                                            u_info["username"]
                                        ).strip()

                                    candidates = first_item.get(
                                        "image_versions2", {}
                                    ).get("candidates", [])
                                    if candidates:
                                        best_cand = max(
                                            [
                                                c
                                                for c in candidates
                                                if isinstance(c, dict)
                                            ],
                                            key=lambda c: int(c.get("width", 0))
                                            * int(c.get("height", 0)),
                                            default=candidates[0],
                                        )
                                        cdn_url = best_cand.get("url")
                    except Exception as exc:
                        logger.debug("Mobile API media info error: %s", exc)

            if not cdn_url or not detected_username:
                embed_url = (
                    f"https://www.instagram.com/p/{self.shortcode}/embed/captioned/"
                )
                html_text = ""
                try:
                    headers = {
                        "Referer": "https://www.instagram.com/",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                    if cffi_requests is not None:
                        resp = cffi_requests.get(
                            embed_url,
                            impersonate="chrome120",
                            headers=headers,
                            cookies=cookie_dict,
                            timeout=5.0,
                        )
                        if resp.status_code == 200:
                            html_text = resp.text
                    else:
                        if self.cookie_str:
                            headers["Cookie"] = self.cookie_str
                        req = urllib.request.Request(embed_url, headers=headers)
                        with urllib.request.urlopen(req, timeout=5.0) as resp:
                            html_text = resp.read().decode("utf-8", errors="replace")

                    if html_text:
                        clean_html = html_text.replace(r"\/", "/").replace(
                            r"\u0026", "&"
                        )
                        clean_html = html_lib.unescape(clean_html)

                        if not detected_username:
                            user_patterns = [
                                r'<a[^>]+class=["\'][^"\']*CaptionUsername[^"\']*["\'][^>]*>\s*@?([a-zA-Z0-9_\.]+)\s*</a>',
                                r'<a[^>]+class=["\'][^"\']*(?:Username|Avatar|Header)[^"\']*["\'][^>]*href=["\'](?:https?://(?:www\.)?instagram\.com)?/([a-zA-Z0-9_\.]+)/?',
                                r'<header[^>]*>.*?<a[^>]+href=["\'](?:https?://(?:www\.)?instagram\.com)?/([a-zA-Z0-9_\.]+)/?',
                                r'["\']owner["\']\s*:\s*\{[^}]*["\']username["\']\s*:\s*["\']([a-zA-Z0-9_\.]+)["\']',
                                r'\\?"username\\?"\s*:\s*\\?"([a-zA-Z0-9_\.]+)\\?"',
                            ]
                            for u_pat in user_patterns:
                                um = re.search(
                                    u_pat, clean_html, re.DOTALL | re.IGNORECASE
                                )
                                if um:
                                    cand_user = um.group(1).strip()
                                    if cand_user.lower() not in (
                                        "p",
                                        "reel",
                                        "reels",
                                        "stories",
                                        "explore",
                                        "developer",
                                        "about",
                                        "legal",
                                        "instagram",
                                    ):
                                        detected_username = cand_user
                                        break

                        if not cdn_url:
                            img_patterns = [
                                r'["\']display_url["\']\s*:\s*["\'](https://[^"\']+(?:cdninstagram\.com|fbcdn\.net)[^"\']+)["\']',
                                r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
                                r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                                r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\']',
                            ]
                            for pat in img_patterns:
                                m = re.search(pat, clean_html, re.IGNORECASE)
                                if m:
                                    cand_img = m.group(1).replace("&amp;", "&")
                                    if "150x150" not in cand_img:
                                        cdn_url = cand_img
                                        break
                except Exception as exc:
                    logger.debug("Embed preview lookup error: %s", exc)

        if detected_username:
            GLOBAL_URL_USER_CACHE[self.raw_url] = detected_username

        if cdn_url:
            img_bytes = self._download_image_bytes(cdn_url)
            if img_bytes:
                GLOBAL_THUMB_CACHE.set(self.raw_url, img_bytes)
                self.signals.loaded.emit(
                    self.raw_url, img_bytes, detected_username or ""
                )
        elif detected_username:
            self.signals.loaded.emit(self.raw_url, b"", detected_username)


class URLGlassThumbnailPod(QFrame):
    """Frosted thumbnail pod rendering high-resolution image previews or fallback vector glyphs,

    complete with an enlarged floating preview popup on mouse hover.
    """

    def __init__(self, target_type: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.target_type = target_type.upper()
        self._pixmap: Optional[QPixmap] = None
        self._rendered_pixmap: Optional[QPixmap] = None
        self.view_mode: str = "grid"
        self._preview_popup: Optional[ThumbnailHoverPopup] = None

        self.setMouseTracking(True)
        self.setFixedSize(40, 40)

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        if mode == "grid":
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.setFixedHeight(125)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.setFixedSize(40, 40)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._rebuild_rendered_pixmap()
        self.updateGeometry()
        self.update()

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        if pixmap and not pixmap.isNull():
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rebuild_rendered_pixmap()
        self.update()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._rebuild_rendered_pixmap()

    def _rebuild_rendered_pixmap(self) -> None:
        if not self._pixmap or self._pixmap.isNull():
            self._rendered_pixmap = None
            return

        w, h = max(10, self.width()), max(10, self.height())

        scaled = self._pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, (scaled.width() - w) // 2)
        crop_y = max(0, (scaled.height() - h) // 2)
        cropped = scaled.copy(crop_x, crop_y, w, h)

        target = QPixmap(w, h)
        target.fill(Qt.GlobalColor.transparent)
        p = QPainter(target)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QPainterPath()
        if self.view_mode == "grid":
            r = 12.0
            path.moveTo(0, h)
            path.lineTo(0, r)
            path.quadTo(0, 0, r, 0)
            path.lineTo(w - r, 0)
            path.quadTo(w, 0, w, r)
            path.lineTo(w, h)
            path.closeSubpath()
        else:
            path.addRoundedRect(QRectF(0, 0, w, h), 9.0, 9.0)

        p.setClipPath(path)
        p.drawPixmap(0, 0, cropped)
        p.end()

        self._rendered_pixmap = target

    def enterEvent(self, event: Any) -> None:
        super().enterEvent(event)
        if self._pixmap and not self._pixmap.isNull():
            if not self._preview_popup:
                self._preview_popup = ThumbnailHoverPopup()
            self._preview_popup.set_preview_pixmap(self._pixmap)

            offset_y = -80 if self.view_mode == "grid" else -120
            global_pos = self.mapToGlobal(QPoint(self.width() + 14, offset_y))

            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                if global_pos.x() + 290 > geom.right():
                    global_pos.setX(self.mapToGlobal(QPoint(0, 0)).x() - 300)
                if global_pos.y() + 370 > geom.bottom():
                    global_pos.setY(geom.bottom() - 375)
                if global_pos.y() < geom.top():
                    global_pos.setY(geom.top() + 10)

            self._preview_popup.move(global_pos)
            self._preview_popup.show()

    def leaveEvent(self, event: Any) -> None:
        self.hide_preview()
        super().leaveEvent(event)

    def hideEvent(self, event: Any) -> None:
        self.hide_preview()
        super().hideEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        self.hide_preview()
        super().mousePressEvent(event)

    def hide_preview(self) -> None:
        if self._preview_popup and self._preview_popup.isVisible():
            self._preview_popup.hide()

    def cleanup(self) -> None:
        """Dismisses and cleans up attached hover preview popup."""
        self.hide_preview()
        if self._preview_popup:
            self._preview_popup.deleteLater()
            self._preview_popup = None

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        w, h = float(self.width()), float(self.height())
        path = QPainterPath()

        if self.view_mode == "grid":
            r = 12.0
            path.moveTo(0.5, h)
            path.lineTo(0.5, r)
            path.quadTo(0.5, 0.5, r, 0.5)
            path.lineTo(w - r, 0.5)
            path.quadTo(w - 0.5, 0.5, w - 0.5, r)
            path.lineTo(w - 0.5, h)
            path.closeSubpath()
        else:
            rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
            path.addRoundedRect(rect, 9.0, 9.0)

        # 1. Image Content or Acrylic Backing
        if self._rendered_pixmap:
            painter.drawPixmap(0, 0, self._rendered_pixmap)
        else:
            fill_grad = QLinearGradient(0, 0, 0, h)
            fill_grad.setColorAt(0.0, QColor(32, 28, 48, 200))
            fill_grad.setColorAt(1.0, QColor(18, 16, 26, 230))
            painter.fillPath(path, fill_grad)

            icon_color = "#E1306C" if "REEL" in self.target_type else "#38BDF8"
            icon_name = "search" if "PROFILE" in self.target_type else "link"
            glyph_size = 24 if self.view_mode == "grid" else 18
            icon = get_icon(icon_name, color=icon_color, size=glyph_size)
            if icon and not icon.isNull():
                pix = icon.pixmap(glyph_size, glyph_size)
                painter.drawPixmap(
                    int((w - glyph_size) / 2),
                    int((h - glyph_size) / 2),
                    pix,
                )

        # 2. Specular Edge Stroke
        border_grad = QLinearGradient(0, 0, w, h)
        border_grad.setColorAt(0.0, QColor(255, 255, 255, 80))
        border_grad.setColorAt(0.6, QColor(225, 48, 108, 50))
        border_grad.setColorAt(1.0, QColor(255, 255, 255, 20))
        painter.setPen(QPen(border_grad, 1.0))
        painter.drawPath(path)

        painter.end()


class URLItemCard(QFrame):
    """URL presentation card with dynamic reflow between compact List and card-tile Grid formats."""

    deleted = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        target_type: str = "MEDIA",
        username: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.url = url.strip()
        self.target_type = target_type.upper()
        self.username = username.strip().lstrip("@")
        self.view_mode: str = "grid"

        self.setObjectName("URLItemCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._init_ui()
        self._apply_mode_layout()

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

        self.thumb_pod = URLGlassThumbnailPod(self.target_type, self)

        self.lbl_type = QLabel(self.target_type, self)
        self.lbl_type.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.lbl_type.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_type.setFixedHeight(24)
        self.lbl_type.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg_col};
                color: {text_col};
                border: 1px solid {border_col};
                border-radius: 6px;
                padding: 1px 8px;
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 0.5px;
            }}
            """
        )

        # Author handle label
        self.lbl_username = QLabel(self)
        self.lbl_username.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.lbl_username.setStyleSheet("color: #38BDF8; background: transparent;")
        self.lbl_username.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        if self.username:
            self.lbl_username.setText(f"@{self.username}")
            self.lbl_username.show()
        else:
            self.lbl_username.setText("")
            self.lbl_username.hide()

        clean_url = (
            self.url.replace("https://www.", "")
            .replace("http://www.", "")
            .replace("https://", "")
            .replace("http://", "")
        )
        self.lbl_url = QLabel(clean_url, self)
        self.lbl_url.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        self.lbl_url.setStyleSheet("color: #94A3B8; background: transparent;")
        self.lbl_url.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_url.setToolTip(self.url)

        self.btn_delete = QPushButton(self)
        self.btn_delete.setObjectName("CardDeleteButton")
        self.btn_delete.setFixedSize(30, 30)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setToolTip("Remove target")
        self.btn_delete.setStyleSheet(
            """
            QPushButton#CardDeleteButton {
                background-color: transparent;
                border: none;
                border-radius: 6px;
            }
            QPushButton#CardDeleteButton:hover {
                background-color: rgba(239, 68, 68, 0.25);
            }
            """
        )
        del_icon = get_icon("trash", color="#94A3B8", size=15)
        if del_icon:
            self.btn_delete.setIcon(del_icon)
            self.btn_delete.setIconSize(QSize(15, 15))
        self.btn_delete.clicked.connect(lambda: self.deleted.emit(self.url))

    def _apply_mode_layout(self) -> None:
        """Reconstructs internal card layout cleanly between List row and Grid tile."""
        for w in (
            self.thumb_pod,
            self.lbl_type,
            self.lbl_username,
            self.lbl_url,
            self.btn_delete,
        ):
            w.setParent(self)

        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)

        self.thumb_pod.set_view_mode(self.view_mode)

        if self.view_mode == "grid":
            self.setFixedHeight(215)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 10)
            layout.setSpacing(6)

            # 1. Full-width preview image banner
            layout.addWidget(self.thumb_pod)

            # 2. Meta row: badge on left, trash on right
            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(10, 0, 10, 0)
            meta_row.setSpacing(6)
            self.lbl_type.setFixedWidth(78)
            meta_row.addWidget(self.lbl_type)
            meta_row.addStretch()
            meta_row.addWidget(self.btn_delete)
            layout.addLayout(meta_row)

            # 3. Instagram username handle
            self.lbl_username.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.lbl_username.setContentsMargins(10, 0, 10, 0)
            layout.addWidget(self.lbl_username)

            # 4. URL description
            self.lbl_url.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
            self.lbl_url.setContentsMargins(10, 0, 10, 0)
            self.lbl_url.setStyleSheet("color: #94A3B8; background: transparent;")
            layout.addWidget(self.lbl_url)

        else:
            self.setFixedHeight(58)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(10, 7, 12, 7)
            layout.setSpacing(12)

            layout.addWidget(self.thumb_pod)
            self.lbl_type.setFixedWidth(82)
            layout.addWidget(self.lbl_type)

            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(2)
            text_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            self.lbl_username.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.lbl_username.setContentsMargins(0, 0, 0, 0)
            text_col.addWidget(self.lbl_username)

            self.lbl_url.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
            self.lbl_url.setContentsMargins(0, 0, 0, 0)
            self.lbl_url.setStyleSheet("color: #94A3B8; background: transparent;")
            text_col.addWidget(self.lbl_url)

            layout.addLayout(text_col, stretch=1)
            layout.addWidget(self.btn_delete)

    def set_view_mode(self, mode: str) -> None:
        if self.view_mode != mode:
            self.view_mode = mode
            self._apply_mode_layout()

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        """Delegates preview pixmap updates directly to the internal thumbnail pod."""
        self.thumb_pod.set_preview_pixmap(pixmap)

    def set_username(self, uname: str) -> None:
        clean = uname.strip().lstrip("@")
        if clean:
            self.username = clean
            self.lbl_username.setText(f"@{clean}")
            self.lbl_username.show()
            self.update()

    def cleanup(self) -> None:
        """Cleans up internal thumbnail pod and dismisses active hover popups."""
        if hasattr(self, "thumb_pod") and self.thumb_pod:
            self.thumb_pod.cleanup()


class URLChipInput(QObject):
    """Deck coordinator orchestrating URL collection, auto-clipboard ingestion, and dual-mode presentation."""

    urls_changed = pyqtSignal()
    view_mode_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.urls: List[str] = []
        self._cards: Dict[str, URLItemCard] = {}
        self.view_mode: str = "grid"
        self.cookie_str: str = ""

        self.preview_signals = PreviewSignals()
        self.preview_signals.loaded.connect(self._on_preview_loaded)

        self._init_input_bar(parent)
        self._init_list_view(parent)

    def set_cookie_str(self, cookie_str: str) -> None:
        self.cookie_str = cookie_str or ""

    def _init_input_bar(self, parent: Optional[QWidget]) -> None:
        self.input_widget = QWidget(parent)
        layout = QHBoxLayout(self.input_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.input_edit = QLineEdit(self.input_widget)
        self.input_edit.setFixedHeight(40)
        self.input_edit.setPlaceholderText(
            "Paste Instagram URLs here (Ctrl+V or Enter multiple links)..."
        )
        self.input_edit.returnPressed.connect(self._on_add_clicked)
        layout.addWidget(self.input_edit, stretch=1)

        self.btn_add = QPushButton(self.input_widget)
        self.btn_add.setObjectName("GlassActionButton")
        self.btn_add.setFixedSize(40, 40)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setToolTip("Add URL to Queue")
        plus_icon = get_icon("plus", color="#FFFFFF", size=18)
        if plus_icon:
            self.btn_add.setIcon(plus_icon)
            self.btn_add.setIconSize(QSize(18, 18))
        self.btn_add.clicked.connect(self._on_add_clicked)
        layout.addWidget(self.btn_add)

    def _init_list_view(self, parent: Optional[QWidget]) -> None:
        self.list_widget = QWidget(parent)
        root_layout = QVBoxLayout(self.list_widget)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # Header Bar: Title + View Mode Toggle + Clear All
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.lbl_list_count = QLabel("URL Links (0)", self.list_widget)
        self.lbl_list_count.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.lbl_list_count.setStyleSheet("color: #FFFFFF;")
        header.addWidget(self.lbl_list_count)

        header.addStretch()

        # View Mode Toggle Button (Grid <-> List)
        self.btn_toggle_view = QPushButton(self.list_widget)
        self.btn_toggle_view.setObjectName("GlassActionButton")
        self.btn_toggle_view.setFixedSize(36, 32)
        self.btn_toggle_view.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_toggle_button()
        self.btn_toggle_view.clicked.connect(self.toggle_view_mode)
        header.addWidget(self.btn_toggle_view)

        # Clear All Button
        self.btn_clear_all = QPushButton(self.list_widget)
        self.btn_clear_all.setObjectName("DestructiveButton")
        self.btn_clear_all.setFixedSize(36, 32)
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.setToolTip("Clear All Links")
        del_icon = get_icon("trash", color="#FF6B6B", size=15)
        if del_icon:
            self.btn_clear_all.setIcon(del_icon)
            self.btn_clear_all.setIconSize(QSize(15, 15))
        self.btn_clear_all.clicked.connect(self.clear)
        header.addWidget(self.btn_clear_all)

        root_layout.addLayout(header)

        # Scroll Viewport with Adaptive Grid Layout
        self.list_scroll = QScrollArea(self.list_widget)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.list_content_widget = QWidget()
        self.deck_layout = QGridLayout(self.list_content_widget)
        self.deck_layout.setContentsMargins(4, 4, 4, 4)
        self.deck_layout.setSpacing(10)

        self.list_scroll.setWidget(self.list_content_widget)

        # Cache viewport reference and attach event filter
        self._viewport = self.list_scroll.viewport()
        if self._viewport is not None:
            self._viewport.installEventFilter(self)

        root_layout.addWidget(self.list_scroll, stretch=1)

    def eventFilter(self, watched: Optional[QObject], event: Optional[QEvent]) -> bool:
        """Dynamically updates grid layout on resize while safely handling C++ teardown."""
        try:
            scroll = getattr(self, "list_scroll", None)
            if scroll is None or watched is None or event is None:
                return super().eventFilter(watched, event)

            viewport = getattr(self, "_viewport", None)
            if viewport is None:
                viewport = scroll.viewport()
                self._viewport = viewport

            if (
                watched is viewport
                and event.type() == QEvent.Type.Resize
                and getattr(self, "view_mode", "grid") == "grid"
            ):
                self._relayout_deck()
        except (RuntimeError, AttributeError):
            return False
        return super().eventFilter(watched, event)

    def set_view_mode(self, mode: str) -> None:
        target_mode = "grid" if mode == "grid" else "list"
        if self.view_mode != target_mode:
            self.view_mode = target_mode
            self._update_toggle_button()
            self._relayout_deck()
            self.view_mode_changed.emit(self.view_mode)

    def toggle_view_mode(self) -> None:
        new_mode = "grid" if self.view_mode == "list" else "list"
        self.set_view_mode(new_mode)

    def _update_toggle_button(self) -> None:
        icon_name = "list" if self.view_mode == "grid" else "grid"
        tip = (
            "Switch to List View" if self.view_mode == "grid" else "Switch to Grid View"
        )
        icon = get_icon(icon_name, color="#CBD5E1", size=15)
        if icon:
            self.btn_toggle_view.setIcon(icon)
            self.btn_toggle_view.setIconSize(QSize(15, 15))
        self.btn_toggle_view.setToolTip(tip)

    def _calculate_columns(self) -> int:
        if getattr(self, "view_mode", "grid") == "list":
            return 1
        try:
            viewport_w = self.list_scroll.viewport().width()
            return max(2, min(4, max(1, viewport_w // 240)))
        except (RuntimeError, AttributeError):
            return 2

    def _relayout_deck(self) -> None:
        self.list_content_widget.setUpdatesEnabled(False)
        try:
            while self.deck_layout.count():
                self.deck_layout.takeAt(0)

            cols = self._calculate_columns()
            for col_idx in range(max(cols, 4)):
                self.deck_layout.setColumnStretch(col_idx, 1 if col_idx < cols else 0)

            for idx, url in enumerate(self.urls):
                card = self._cards.get(url)
                if card:
                    card.set_view_mode(self.view_mode)
                    if self.view_mode == "grid":
                        row = idx // cols
                        col = idx % cols
                        self.deck_layout.addWidget(card, row, col)
                    else:
                        self.deck_layout.addWidget(card, idx, 0)

            row_count = (
                (len(self.urls) + cols - 1) // cols if cols > 0 else len(self.urls)
            )
            self.deck_layout.setRowStretch(row_count, 1)

        finally:
            self.list_content_widget.setUpdatesEnabled(True)

    def add_url_chip(self, url: str) -> None:
        clean = url.strip()
        if not clean or clean in self.urls:
            return

        parsed = parse_instagram_url(clean)
        ttype = parsed.get("type", "media").upper()
        shortcode = parsed.get("shortcode")
        initial_user = parsed.get("username") or GLOBAL_URL_USER_CACHE.get(clean, "")

        card = URLItemCard(
            clean,
            target_type=ttype,
            username=initial_user,
            parent=self.list_content_widget,
        )
        card.set_view_mode(self.view_mode)
        card.deleted.connect(self.remove_url)

        self.urls.append(clean)
        self._cards[clean] = card

        self._relayout_deck()
        self.urls_changed.emit()

        cached_bytes = GLOBAL_THUMB_CACHE.get(clean)
        cached_user = GLOBAL_URL_USER_CACHE.get(clean, "") or initial_user

        # Invalidate low-resolution cache entries on link insertion
        is_high_res = False
        if cached_bytes:
            qimg = QImage()
            if qimg.loadFromData(cached_bytes):
                is_high_res = qimg.width() >= 300 and qimg.height() >= 300

        if cached_bytes and is_high_res:
            self._on_preview_loaded(clean, cached_bytes, cached_user)
        else:
            task = URLPreviewTask(
                raw_url=clean,
                shortcode=shortcode,
                cookie_str=self.cookie_str,
                signals=self.preview_signals,
                username=initial_user,
                target_type=ttype,
            )
            QThreadPool.globalInstance().start(task)

    @pyqtSlot(str, bytes, str)
    def _on_preview_loaded(self, url: str, data: bytes, username: str) -> None:
        card = self._cards.get(url)
        if card:
            if data:
                pix = QPixmap()
                if pix.loadFromData(data):
                    card.set_preview_pixmap(pix)
            if username:
                card.set_username(username)

    def cleanup(self) -> None:
        """Detaches viewport event filters and clears child card popups on widget teardown."""
        try:
            if hasattr(self, "list_scroll") and self.list_scroll is not None:
                vp = self.list_scroll.viewport()
                if vp is not None:
                    vp.removeEventFilter(self)
        except (RuntimeError, AttributeError, TypeError):
            pass

        self.clear()

    def remove_url(self, url: str) -> None:
        if url in self.urls:
            self.urls.remove(url)
            card = self._cards.pop(url, None)
            if card:
                card.cleanup()
                self.deck_layout.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
            self._relayout_deck()
            self.urls_changed.emit()

    def clear(self) -> None:
        self.urls.clear()
        for card in list(self._cards.values()):
            card.cleanup()
            self.deck_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._relayout_deck()
        self.urls_changed.emit()

    def get_targets(self) -> List[str]:
        return list(self.urls)

    def count(self) -> int:
        return len(self.urls)

    def _on_add_clicked(self) -> None:
        raw_text = self.input_edit.text().strip()
        if not raw_text:
            return

        extracted = extract_instagram_urls(raw_text)
        if extracted:
            for u in extracted:
                self.add_url_chip(u)
        else:
            self.add_url_chip(raw_text)

        self.input_edit.clear()
        QTimer.singleShot(60, self.smooth_scroll_to_bottom)

    def smooth_scroll_to_bottom(self) -> None:
        v_bar = self.list_scroll.verticalScrollBar()
        if not v_bar:
            return
        self.list_content_widget.adjustSize()
        target = v_bar.maximum()
        if target > 0:
            anim = QPropertyAnimation(v_bar, b"value", self.list_scroll)
            anim.setDuration(240)
            anim.setStartValue(v_bar.value())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()
