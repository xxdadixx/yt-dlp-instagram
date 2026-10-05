"""
gui/widgets/url_chip_input.py - URL Chip Deck with dual Grid/List presentation modes,
author username extraction, and resilient asynchronous CDN thumbnail previewing.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import ssl
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
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    import curl_cffi
    import curl_cffi.curl
    import curl_cffi.requests
    import curl_cffi.requests.exceptions

    cffi_requests: Any = curl_cffi.requests
    CffiTimeout: type[BaseException] = curl_cffi.requests.exceptions.Timeout
    CffiCurlError: type[BaseException] = curl_cffi.curl.CurlError
except Exception:
    curl_cffi = None  # pyright: ignore[reportConstantRedefinition]
    cffi_requests = None
    CffiTimeout = TimeoutError
    CffiCurlError = TimeoutError

logger = logging.getLogger(__name__)

# Shared memory cache for extracted author usernames
GLOBAL_URL_USER_CACHE: Dict[str, str] = {}


class PreviewSignals(QObject):
    # Use object instead of bytes to prevent PyQt6 C++ const char* null-byte truncation
    loaded = pyqtSignal(str, object, str)


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

    Mobile Media Info API, IPv4-pinned curl_cffi requests, and yt-dlp metadata fallbacks.
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
        self._is_cancelled: bool = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._is_cancelled = True

    def requestInterruption(self) -> None:
        self._is_cancelled = True

    def isInterruptionRequested(self) -> bool:
        return self._is_cancelled

    def _get_ssl_context(self) -> ssl.SSLContext:
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl._create_unverified_context()

    def _download_image_bytes(self, cdn_url: str) -> Optional[bytes]:
        """Streams preview image bytes safely across TLS boundaries."""
        if not cdn_url or self._is_cancelled:
            return None

        clean_url = cdn_url.replace("&amp;", "&")

        # 1. Primary fast stream via native urllib
        try:
            req = urllib.request.Request(
                clean_url,
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
            with urllib.request.urlopen(
                req, context=self._get_ssl_context(), timeout=8.0
            ) as resp:
                data = resp.read()
                if data and len(data) > 64:
                    return data
        except Exception as exc:
            logger.debug("urllib CDN stream error: %s", exc)

        if self._is_cancelled:
            return None

        # 2. Fallback stream via curl_cffi Chrome impersonation
        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    clean_url,
                    impersonate="chrome120",
                    timeout=8.0,
                )
                if resp.status_code == 200 and resp.content and len(resp.content) > 64:
                    return bytes(resp.content)
        except Exception as exc:
            logger.debug("cffi CDN stream error: %s", exc)

        return None

    def _fetch_mobile_media_info(self, shortcode: str) -> tuple[str, str]:
        """Fetches high-resolution media thumbnail and creator username using

        Instagram's native Mobile Media Info endpoint (i.instagram.com).
        """
        media_id = shortcode_to_id(shortcode)
        if not media_id:
            return "", ""

        url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        headers = {
            "User-Agent": (
                "Instagram 315.0.0.38.109 Android (33/13; 420dpi; 1080x2400; "
                "samsung; SM-G991N; o1s; exynos2100; en_US; 564998762)"
            ),
            "X-IG-App-ID": "936619743392459",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        raw_json_text = ""
        # 1. Primary attempt via curl_cffi with forced IPv4
        try:
            if cffi_requests is not None:
                curl_opts = {}
                if curl_cffi and hasattr(curl_cffi, "curl"):
                    if hasattr(curl_cffi.curl, "CURLOPT_IPRESOLVE") and hasattr(
                        curl_cffi.curl, "CURL_IPRESOLVE_V4"
                    ):
                        curl_opts[curl_cffi.curl.CURLOPT_IPRESOLVE] = (
                            curl_cffi.curl.CURL_IPRESOLVE_V4
                        )

                resp = cffi_requests.get(
                    url,
                    headers=headers,
                    impersonate="chrome120",
                    timeout=7.0,
                    curl_options=curl_opts if curl_opts else None,
                )
                if resp.status_code == 200 and resp.text:
                    raw_json_text = resp.text
        except Exception as exc:
            logger.debug("Mobile Media Info curl_cffi error for %s: %s", shortcode, exc)

        # 2. Fallback attempt via native urllib
        if not raw_json_text and not self._is_cancelled:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(
                    req, context=self._get_ssl_context(), timeout=7.0
                ) as resp:
                    raw_bytes = resp.read()
                    content_encoding = resp.headers.get("Content-Encoding", "").lower()
                    if "gzip" in content_encoding or (
                        len(raw_bytes) >= 2 and raw_bytes[:2] == b"\x1f\x8b"
                    ):
                        import gzip

                        raw_bytes = gzip.decompress(raw_bytes)
                    elif "deflate" in content_encoding:
                        import zlib

                        try:
                            raw_bytes = zlib.decompress(raw_bytes)
                        except Exception:
                            raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
                    charset = resp.headers.get_content_charset() or "utf-8"
                    raw_json_text = raw_bytes.decode(charset, errors="replace")
            except Exception as exc:
                logger.debug(
                    "Mobile Media Info urllib error for %s: %s", shortcode, exc
                )

        if not raw_json_text:
            return "", ""

        try:
            data = json.loads(raw_json_text)
            if not isinstance(data, dict):
                return "", ""
            items = data.get("items")
            if (
                not isinstance(items, list)
                or not items
                or not isinstance(items[0], dict)
            ):
                return "", ""
            item = items[0]

            author = ""
            user = item.get("user")
            if isinstance(user, dict) and user.get("username"):
                author = str(user["username"]).strip()

            thumb_url = ""
            img_v2 = item.get("image_versions2")
            if isinstance(img_v2, dict) and isinstance(img_v2.get("candidates"), list):
                cands = [
                    c
                    for c in img_v2["candidates"]
                    if isinstance(c, dict) and c.get("url")
                ]
                if cands:
                    best = max(
                        cands,
                        key=lambda c: int(c.get("width", 0)) * int(c.get("height", 0)),
                    )
                    thumb_url = str(best.get("url") or "")

            return thumb_url, author
        except Exception as exc:
            logger.debug("Failed to parse Mobile Media Info JSON: %s", exc)
            return "", ""

    def _fetch_html(self, url: str) -> Optional[str]:
        """Fetches page markup with IPv4-pinned curl_cffi and automatic decompression."""
        if self._is_cancelled:
            return None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
        }

        # 1. Primary Engine: curl_cffi with forced IPv4
        try:
            if cffi_requests is not None:
                curl_opts = {}
                if curl_cffi and hasattr(curl_cffi, "curl"):
                    if hasattr(curl_cffi.curl, "CURLOPT_IPRESOLVE") and hasattr(
                        curl_cffi.curl, "CURL_IPRESOLVE_V4"
                    ):
                        curl_opts[curl_cffi.curl.CURLOPT_IPRESOLVE] = (
                            curl_cffi.curl.CURL_IPRESOLVE_V4
                        )

                resp = cffi_requests.get(
                    url,
                    headers=headers,
                    impersonate="chrome120",
                    timeout=8.0,
                    curl_options=curl_opts if curl_opts else None,
                )
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception as exc:
            logger.debug("curl_cffi HTML fetch error for %s: %s", url, exc)

        if self._is_cancelled:
            return None

        # 2. Secondary Engine: urllib fallback
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(
                req, context=self._get_ssl_context(), timeout=8.0
            ) as resp:
                raw_bytes = resp.read()
                content_encoding = resp.headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or (
                    len(raw_bytes) >= 2 and raw_bytes[:2] == b"\x1f\x8b"
                ):
                    import gzip

                    raw_bytes = gzip.decompress(raw_bytes)
                elif "deflate" in content_encoding:
                    import zlib

                    try:
                        raw_bytes = zlib.decompress(raw_bytes)
                    except Exception:
                        raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)

                charset = resp.headers.get_content_charset() or "utf-8"
                return raw_bytes.decode(charset, errors="replace")
        except Exception as exc:
            logger.debug("urllib HTML fetch error for %s: %s", url, exc)

        return None

    def _extract_preview_from_html(
        self, html_text: str, shortcode: str
    ) -> tuple[str, str]:
        """Extracts thumbnail CDN URL and author handle from Instagram markup."""
        if not html_text:
            return "", ""

        clean_html = html_text.replace(r"\/", "/").replace(r"\u0026", "&")
        clean_html = html_lib.unescape(clean_html)

        thumb_url = ""
        username = ""

        # Strategy A: window.__additionalDataLoaded state payload
        match_add = re.search(
            r"window\.__additionalDataLoaded\([^,]+,\s*(\{.+?\})\s*\);",
            clean_html,
            re.DOTALL,
        )
        if match_add:
            try:
                data = json.loads(match_add.group(1))
                media = (
                    data.get("graphql", {}).get("shortcode_media")
                    or data.get("data", {}).get("xdt_shortcode_media")
                    or data.get("shortcode_media")
                )
                if isinstance(media, dict):
                    thumb_url = str(
                        media.get("display_url")
                        or media.get("display_src")
                        or media.get("thumbnail_src")
                        or ""
                    )
                    owner = media.get("owner")
                    if isinstance(owner, dict):
                        username = str(owner.get("username") or "")
            except Exception:
                pass

        # Strategy B: EmbeddedMediaImage and fallback img tags
        if not thumb_url:
            img_match = (
                re.search(
                    r'<img[^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                    clean_html,
                    re.IGNORECASE,
                )
                or re.search(
                    r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*EmbeddedMediaImage[^"\']*["\']',
                    clean_html,
                    re.IGNORECASE,
                )
                or re.search(
                    r'<img[^>]+src=["\'](https?://[^"\']*(?:cdninstagram\.com|fbcdn\.net)[^"\']*)["\']',
                    clean_html,
                    re.IGNORECASE,
                )
            )
            if img_match:
                thumb_url = img_match.group(1).replace("&amp;", "&")

        # Strategy C: OpenGraph og:image fallback
        if not thumb_url:
            m_og = re.search(
                r'<meta\s+property=["\']og:image["\']\s+content=["\'](https?://[^"\']+)["\']',
                clean_html,
                re.IGNORECASE,
            )
            if m_og:
                thumb_url = m_og.group(1).replace("&amp;", "&")

        # Author handle resolution
        if not username:
            cap_m = re.search(
                r'<a[^>]+class=["\'][^"\']*CaptionUsername[^"\']*["\'][^>]*>\s*@?([a-zA-Z0-9_\.]+)\s*</a>',
                clean_html,
                re.IGNORECASE,
            )
            if cap_m:
                username = cap_m.group(1).strip()
            else:
                hdr_m = re.search(
                    r'<a[^>]+href=["\'](?:https?://(?:www\.)?instagram\.com)?/([a-zA-Z0-9_\.]+)/?(?:\?[^"\']*)?["\'][^>]*class=["\'][^"\']*(?:Username|Avatar|Header|CaptionUsername)[^"\']*["\']',
                    clean_html,
                    re.IGNORECASE,
                )
                if hdr_m:
                    cand = hdr_m.group(1).strip()
                    if cand.lower() not in (
                        "p",
                        "reel",
                        "reels",
                        "tv",
                        "stories",
                        "explore",
                    ):
                        username = cand

        return thumb_url, username

    def _resolve_profile_picture(self, clean_user: str) -> Optional[str]:
        """Resolves the uncompressed HD profile avatar CDN URL strictly for clean_user."""
        target = clean_user.lower().strip().lstrip("@")

        # 1. Native Mobile API
        url = f"https://i.instagram.com/api/v1/users/{target}/usernameinfo/"
        headers = {
            "User-Agent": (
                "Instagram 315.0.0.38.109 Android (33/13; 420dpi; 1080x2400; "
                "samsung; SM-G991N; o1s; exynos2100; en_US; 564998762)"
            ),
            "X-IG-App-ID": "936619743392459",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        try:
            if cffi_requests is not None:
                resp = cffi_requests.get(
                    url,
                    headers=headers,
                    impersonate="chrome120",
                    timeout=6.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    user = data.get("user")
                    if isinstance(user, dict):
                        hd_info = user.get("hd_profile_pic_url_info")
                        if isinstance(hd_info, dict) and hd_info.get("url"):
                            return str(hd_info["url"])
                        hd = user.get("profile_pic_url_hd") or user.get(
                            "profile_pic_url"
                        )
                        if hd:
                            return str(hd)
        except Exception as exc:
            logger.debug("Mobile usernameinfo avatar error for @%s: %s", target, exc)

        # 2. SSR HTML Fallback
        page_url = f"https://www.instagram.com/{target}/"
        html_text = self._fetch_html(page_url)
        if html_text:
            clean_html = html_text.replace(r"\/", "/").replace(r"\u0026", "&")
            clean_html = html_lib.unescape(clean_html)

            m_og = re.search(
                r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
                clean_html,
                re.IGNORECASE,
            )
            if m_og:
                cand = m_og.group(1).replace("&amp;", "&")
                if cand.startswith("http"):
                    return cand

        return None

    def run(self) -> None:
        """Executes thumbnail and username resolution across multi-tiered backends."""
        if self.isInterruptionRequested() or self._is_cancelled:
            return

        # ---------------------------------------------------------------------
        # Case 1: Profile Targets (@username or /username/)
        # ---------------------------------------------------------------------
        clean_user = (self.username or "").strip().lstrip("@")
        if self.target_type in ("PROFILE", "PROFILE_REELS") or (
            not self.shortcode and clean_user
        ):
            if clean_user:
                avatar_url = self._resolve_profile_picture(clean_user)
                if avatar_url and not (
                    self.isInterruptionRequested() or self._is_cancelled
                ):
                    img_bytes = self._download_image_bytes(avatar_url)
                    if img_bytes and not (
                        self.isInterruptionRequested() or self._is_cancelled
                    ):
                        GLOBAL_THUMB_CACHE.set(self.raw_url, img_bytes)
                        GLOBAL_URL_USER_CACHE[self.raw_url] = clean_user
                        self.signals.loaded.emit(self.raw_url, img_bytes, clean_user)
            return

        # ---------------------------------------------------------------------
        # Case 2: Media Targets (Posts, Reels, Carousels)
        # ---------------------------------------------------------------------
        if not self.shortcode:
            return

        thumb_url = ""
        detected_user = self.username or ""

        # Tier 1: Fast Native Mobile API (Exact Image + Exact Handle)
        t1_thumb, t1_user = self._fetch_mobile_media_info(self.shortcode)
        if t1_thumb:
            thumb_url = t1_thumb
            if t1_user:
                detected_user = t1_user

        # Tier 2: Public Web Embed Scraper
        if not thumb_url and not (self.isInterruptionRequested() or self._is_cancelled):
            embed_urls = [
                f"https://www.instagram.com/p/{self.shortcode}/embed/captioned/",
                f"https://www.instagram.com/reel/{self.shortcode}/embed/captioned/",
            ]
            for e_url in embed_urls:
                if self.isInterruptionRequested() or self._is_cancelled:
                    return

                html_text = self._fetch_html(e_url)
                if html_text:
                    t_url, u_name = self._extract_preview_from_html(
                        html_text, self.shortcode
                    )
                    if t_url:
                        thumb_url = t_url
                        if u_name:
                            detected_user = u_name
                        break

        # Tier 3: yt-dlp Flat Metadata Fallback
        if (
            not thumb_url
            and yt_dlp is not None
            and not (self.isInterruptionRequested() or self._is_cancelled)
        ):
            try:
                ydl_opts = {
                    "extract_flat": True,
                    "skip_download": True,
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 6,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self.raw_url, download=False)
                    if isinstance(info, dict):
                        thumb_url = str(info.get("thumbnail") or "")
                        if not detected_user:
                            detected_user = str(info.get("uploader") or "")
            except Exception:
                pass

        if self.isInterruptionRequested() or self._is_cancelled:
            return

        # ---------------------------------------------------------------------
        # Emit Payload to Main Window URL Card
        # ---------------------------------------------------------------------
        if thumb_url:
            img_bytes = self._download_image_bytes(thumb_url)
            if img_bytes and not (
                self.isInterruptionRequested() or self._is_cancelled
            ):
                GLOBAL_THUMB_CACHE.set(self.raw_url, img_bytes)
                if detected_user:
                    GLOBAL_URL_USER_CACHE[self.raw_url] = detected_user
                self.signals.loaded.emit(self.raw_url, img_bytes, detected_user)
        elif detected_user and not (
            self.isInterruptionRequested() or self._is_cancelled
        ):
            self.signals.loaded.emit(self.raw_url, None, detected_user)


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
        self.set_view_mode("grid")

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

        # Self-healing pixmap rebuild if pod geometry changed since initialization
        if self._pixmap and not self._pixmap.isNull():
            if (
                not self._rendered_pixmap
                or self._rendered_pixmap.width() != self.width()
                or self._rendered_pixmap.height() != self.height()
            ):
                self._rebuild_rendered_pixmap()

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

            layout.addWidget(self.thumb_pod)

            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(10, 0, 10, 0)
            meta_row.setSpacing(6)
            self.lbl_type.setFixedWidth(78)
            meta_row.addWidget(self.lbl_type)
            meta_row.addStretch()
            meta_row.addWidget(self.btn_delete)
            layout.addLayout(meta_row)

            self.lbl_username.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.lbl_username.setContentsMargins(10, 0, 10, 0)
            layout.addWidget(self.lbl_username)

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
        self.thumb_pod.set_preview_pixmap(pixmap)

    def set_username(self, uname: str) -> None:
        clean = uname.strip().lstrip("@")
        if clean:
            self.username = clean
            self.lbl_username.setText(f"@{clean}")
            self.lbl_username.show()
            self.update()

    def cleanup(self) -> None:
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
        self._preview_workers: set[URLPreviewTask] = set()
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

        if cached_bytes:
            self._on_preview_loaded(clean, cached_bytes, cached_user)

        task = URLPreviewTask(
            raw_url=clean,
            shortcode=shortcode,
            cookie_str=self.cookie_str,
            signals=self.preview_signals,
            username=initial_user,
            target_type=ttype,
        )
        self._preview_workers.add(task)
        QThreadPool.globalInstance().start(task)

    @pyqtSlot(str, object, str)
    def _on_preview_loaded(self, url: str, data: object, username: str) -> None:
        card = self._cards.get(url)
        if card:
            if isinstance(data, (bytes, bytearray)) and len(data) > 0:
                pix = QPixmap()
                if pix.loadFromData(data):
                    card.set_preview_pixmap(pix)
            if username:
                card.set_username(username)

    def cleanup(self) -> None:
        if hasattr(self, "_preview_workers"):
            for worker in list(self._preview_workers):
                try:
                    worker.cancel()
                except Exception:
                    pass
            self._preview_workers.clear()

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