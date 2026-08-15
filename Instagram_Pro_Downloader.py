import ctypes
import http.cookiejar
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from PyQt6.QtCore import QLocale, QSettings, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QIcon, QImage, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
import yt_dlp

TRANSLATIONS = {
    "th": {
        "title": "Instagram Pro Downloader - Studio Inspector",
        "url_group": "Instagram URLs (รองรับ Posts, Reels, Carousels, Stories และ Highlights)",
        "url_placeholder": "วางลิงก์ Instagram ที่นี่ (รองรับทั้ง /p/, /reel/, และ /stories/user/id/)...",
        "clipboard_chk": "⚡ ตรวจจับคลิปบอร์ดอัตโนมัติ (คัดลอกลิงก์ IG แล้วนำมาวางทันที)",
        "clipboard_tooltip": "เมื่อเปิดใช้งาน: ทันทีที่คุณ Copy ลิงก์ Instagram โปรแกรมจะนำมาต่อท้ายในช่องให้อัตโนมัติ",
        "btn_inspect": "🔍 ตรวจสอบลิงก์ (Inspect)",
        "btn_clear_input": "🧹 ล้างช่องข้อความ",
        "grid_group": "รายการมีเดียในคิว (Media Grid Inspector)",
        "btn_select_all": "เลือกทั้งหมด (Ctrl+A)",
        "btn_delete_selected": "ลบที่เลือก (Del)",
        "btn_clear_completed": "ล้างเฉพาะที่โหลดเสร็จ",
        "lbl_selection_format": "เลือกอยู่: {selected} / {total} รายการ",
        "settings_group": "การจัดเก็บและการตั้งค่าระบบ",
        "save_path_prefix": "โฟลเดอร์บันทึก: ",
        "btn_browse": "เลือกโฟลเดอร์...",
        "btn_open": "เปิดโฟลเดอร์",
        "btn_import_cookie": "🍪 นำเข้า Cookie",
        "btn_import_cookie_tooltip": "นำเข้า cookies.txt สำหรับดูด Stories และ Private Posts",
        "btn_clear_cookie": "🗑️ ลบ Cookie",
        "btn_download_all": "🚀 เริ่มดาวน์โหลดทั้งหมดในคิว",
        "btn_cancel": "⏹️ ยกเลิก",
        "status_ready": "พร้อมทำงาน",
        "status_inspecting": "กำลังตรวจสอบโครงสร้างมีเดีย...",
        "status_downloading": "กำลังดาวน์โหลดไฟล์...",
        "status_cancelled": "⏹️ ยกเลิกการทำงานแล้ว",
        "cookie_connected": "🍪 Cookie: เชื่อมต่อแล้ว (Instagram)",
        "cookie_none": "🍪 Cookie: ไม่มี (โหมดสาธารณะ)",
        "badge_photo": "🖼️ ภาพเดี่ยว",
        "badge_carousel": "📚 อัลบั้ม ({count} มีเดีย)",
        "badge_video": "🎬 วิดีโอ / Reel",
        "badge_story": "✨ Story",
        "lbl_quality": "ความละเอียด:",
        "footer_clip_detected": "📋 ตรวจพบและวางลิงก์ Instagram จากคลิปบอร์ดแล้ว!",
        "warn_no_url": "กรุณาวางลิงก์ Instagram ก่อนทำการตรวจสอบ",
        "warn_story_need_cookie": "⚠️ การดาวน์โหลด Instagram Story จำเป็นต้องนำเข้าไฟล์ cookies.txt ก่อนใช้งาน",
        "warn_need_ffmpeg": "โหมดแปลงเสียง (MP3) จำเป็นต้องมี ffmpeg.exe ในระบบ",
        "cookie_warn_title": "คำเตือนความปลอดภัย (Cookie Security)",
        "cookie_warn_msg": "⚠️ ข้อควรทราบก่อนนำเข้าไฟล์ Cookie:\n\n1. ไฟล์ cookies.txt บรรจุข้อมูล Session การล็อกอินของคุณ ห้ามส่งต่อให้ผู้อื่น\n2. โปรแกรมจะกรองบันทึก 'เฉพาะ Instagram/Facebook' และตัด Cookie เว็บอื่นทิ้งทั้งหมด\n\nคุณต้องการเลือกไฟล์ cookies.txt ต่อหรือไม่?",
        "cookie_clear_title": "ยืนยันการลบ Cookie",
        "cookie_clear_msg": "คุณต้องการลบไฟล์ Cookie ออกจากระบบอย่างถาวรใช่หรือไม่?",
        "cookie_import_success": "บันทึกคุกกี้ Instagram สำเร็จ ({count} รายการ)",
        "cookie_import_fail": "ไม่พบคุกกี้ที่ถูกต้องของ Instagram/Facebook ในไฟล์นี้",
        "inspect_done": "ตรวจสอบเสร็จสิ้น! พบ {count} รายการพร้อมดาวน์โหลด",
        "success_title": "เสร็จสมบูรณ์",
        "success_msg": "ดาวน์โหลดสำเร็จ {success} รายการ\nบันทึกไว้ที่: {path}",
    },
    "en": {
        "title": "Instagram Pro Downloader - Studio Inspector",
        "url_group": "Instagram URLs (Supports Posts, Reels, Carousels, Stories & Highlights)",
        "url_placeholder": "Paste Instagram URLs here (Supports /p/, /reel/, and /stories/user/id/)...",
        "clipboard_chk": "⚡ Auto Clipboard Monitor (Auto-paste when IG link is copied)",
        "clipboard_tooltip": "When enabled: Automatically detects and appends copied Instagram links",
        "btn_inspect": "🔍 Inspect Media",
        "btn_clear_input": "🧹 Clear Textbox",
        "grid_group": "Media Queue Cards (Media Grid Inspector)",
        "btn_select_all": "Select All (Ctrl+A)",
        "btn_delete_selected": "Delete Selected (Del)",
        "btn_clear_completed": "Clear Completed",
        "lbl_selection_format": "Selected: {selected} / {total} items",
        "settings_group": "Storage & System Configuration",
        "save_path_prefix": "Save Folder: ",
        "btn_browse": "Browse...",
        "btn_open": "Open Folder",
        "btn_import_cookie": "🍪 Import Cookie",
        "btn_import_cookie_tooltip": "Import cookies.txt for Stories & Private Posts",
        "btn_clear_cookie": "🗑️ Clear Cookie",
        "btn_download_all": "🚀 Download All in Queue",
        "btn_cancel": "⏹️ Cancel",
        "status_ready": "Ready",
        "status_inspecting": "Inspecting media payloads...",
        "status_downloading": "Downloading files...",
        "status_cancelled": "⏹️ Operation Cancelled",
        "cookie_connected": "🍪 Cookie: Connected (Instagram)",
        "cookie_none": "🍪 Cookie: None (Public Mode)",
        "badge_photo": "🖼️ Single Photo",
        "badge_carousel": "📚 Carousel ({count} Items)",
        "badge_video": "🎬 Video / Reel",
        "badge_story": "✨ Story",
        "lbl_quality": "Quality / Format:",
        "footer_clip_detected": "📋 Instagram link detected and pasted from clipboard!",
        "warn_no_url": "Please paste Instagram URL(s) first.",
        "warn_story_need_cookie": "⚠️ Downloading Instagram Stories requires imported cookies.txt.",
        "warn_need_ffmpeg": "Audio extraction (MP3) requires ffmpeg.exe.",
        "cookie_warn_title": "Cookie Security Warning",
        "cookie_warn_msg": "⚠️ Important Notice:\n\n1. cookies.txt contains your session credentials. Never share it.\n2. The app sanitizes and keeps ONLY Instagram/Facebook cookies.\n\nDo you want to proceed and select cookies.txt?",
        "cookie_clear_title": "Confirm Cookie Deletion",
        "cookie_clear_msg": "Are you sure you want to permanently delete the cookie file?",
        "cookie_import_success": "Sanitized and stored Instagram cookies ({count} entries).",
        "cookie_import_fail": "No valid Instagram/Facebook cookies found in this file.",
        "inspect_done": "Inspection completed! Found {count} items ready.",
        "success_title": "Download Complete",
        "success_msg": "Successfully downloaded {success} item(s)!\nSaved to: {path}",
    },
}

INSTAGRAM_URL_REGEX = r"https?://(?:www\.)?instagram\.com/(?:stories/[A-Za-z0-9_\-\.]+/[0-9]+|stories/highlights/[0-9]+|reel/[A-Za-z0-9_\-\.]+|reels/[A-Za-z0-9_\-\.]+|p/[A-Za-z0-9_\-\.]+|tv/[A-Za-z0-9_\-\.]+)/?"
MOBILE_UA = "Instagram 278.0.0.19.115 Android (30/11; 480dpi; 1080x2176; samsung; SM-G991B; o1s; exynos2100; en_US; 458364234)"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def sanitize_filename(filename: str) -> str:
    """ทำความสะอาดชื่อไฟล์ ตัดอักขระต้องห้ามของระบบปฏิบัติการ Windows"""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()


def shortcode_to_media_id(shortcode: str) -> int:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        if char in alphabet:
            media_id = media_id * 64 + alphabet.index(char)
    return media_id


def parse_instagram_url(url: str) -> dict | None:
    """วิเคราะห์และแยกประเภท URL ของ Instagram ครบทุกรูปแบบอย่างแม่นยำ"""
    # 1. User Story: /stories/{username}/{story_id}
    story_match = re.search(r"instagram\.com/stories/([A-Za-z0-9_\-\.]+)/([0-9]+)", url)
    if story_match:
        username = story_match.group(1)
        story_id = story_match.group(2)
        return {
            "type": "story",
            "username": username,
            "story_id": story_id,
            "media_id": int(story_id),
            "identifier": f"{username}_{story_id}",
            "clean_url": f"https://www.instagram.com/stories/{username}/{story_id}/",
        }

    # 2. Highlight Story: /stories/highlights/{highlight_id}
    hl_match = re.search(r"instagram\.com/stories/highlights/([0-9]+)", url)
    if hl_match:
        hl_id = hl_match.group(1)
        return {
            "type": "highlight",
            "username": "highlight",
            "story_id": hl_id,
            "media_id": int(hl_id),
            "identifier": f"highlight_{hl_id}",
            "clean_url": f"https://www.instagram.com/stories/highlights/{hl_id}/",
        }

    # 3. Reel / Reels / TV: /(reel|reels|tv)/{shortcode}
    reel_match = re.search(r"instagram\.com/(?:reel|reels|tv)/([A-Za-z0-9_\-\.]+)", url)
    if reel_match:
        shortcode = reel_match.group(1).rstrip("/")
        return {
            "type": "video",
            "username": "Instagram",
            "shortcode": shortcode,
            "media_id": shortcode_to_media_id(shortcode),
            "identifier": shortcode,
            "clean_url": f"https://www.instagram.com/reel/{shortcode}/",
        }

    # 4. Standard Post: /p/{shortcode}
    post_match = re.search(r"instagram\.com/p/([A-Za-z0-9_\-\.]+)", url)
    if post_match:
        shortcode = post_match.group(1).rstrip("/")
        return {
            "type": "post",
            "username": "Instagram",
            "shortcode": shortcode,
            "media_id": shortcode_to_media_id(shortcode),
            "identifier": shortcode,
            "clean_url": f"https://www.instagram.com/p/{shortcode}/",
        }

    return None


def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", get_app_dir())
    return get_app_dir()


def get_icon_path() -> str:
    res_icon = os.path.join(get_resource_dir(), "app.ico")
    if os.path.exists(res_icon):
        return res_icon
    return os.path.join(get_app_dir(), "app.ico")


def get_ffmpeg_dir() -> str | None:
    candidates = [
        get_resource_dir(),
        get_app_dir(),
        os.path.join(get_app_dir(), "ffmpeg"),
        os.path.join(get_app_dir(), "ffmpeg", "bin"),
    ]
    for folder in candidates:
        if os.path.exists(os.path.join(folder, "ffmpeg.exe")):
            return folder
    system_ffmpeg = shutil.which("ffmpeg")
    return os.path.dirname(system_ffmpeg) if system_ffmpeg else None


def sanitize_and_save_instagram_cookies(
    src_path: str, dest_path: str, lang: str = "th"
) -> tuple[bool, str]:
    try:
        filtered_lines = [
            "# Netscape HTTP Cookie File\n",
            "# Generated by Instagram Pro Downloader (Sanitized)\n",
        ]
        cookie_count = 0
        valid_domains = [
            "instagram.com",
            ".instagram.com",
            "cdninstagram.com",
            ".cdninstagram.com",
            "facebook.com",
            ".facebook.com",
            "threads.net",
            ".threads.net",
        ]

        with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue

                check_line = line
                if check_line.startswith("#HttpOnly_"):
                    check_line = check_line[len("#HttpOnly_") :]
                elif check_line.startswith("#"):
                    continue

                parts = check_line.split("\t")
                if len(parts) >= 7:
                    domain = parts[0].lower()
                    if any(domain == d or domain.endswith(d) for d in valid_domains):
                        filtered_lines.append(
                            raw_line if raw_line.endswith("\n") else raw_line + "\n"
                        )
                        cookie_count += 1

        if cookie_count == 0:
            return False, TRANSLATIONS[lang]["cookie_import_fail"]

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as f:
            f.writelines(filtered_lines)

        return True, TRANSLATIONS[lang]["cookie_import_success"].format(
            count=cookie_count
        )
    except Exception as e:
        return False, f"Error: {e}"


class SilentLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class NoScrollComboBox(QComboBox):
    def wheelEvent(self, e):
        e.ignore()


class ThumbnailLoader(QThread):
    loaded = pyqtSignal(QPixmap)

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self._is_running = True

    def cancel(self):
        self._is_running = False

    def run(self):
        if not self._is_running or not self.url:
            return
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": DESKTOP_UA})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if not self._is_running:
                    return
                raw_data = resp.read()
                image = QImage.fromData(raw_data)
                if not image.isNull() and self._is_running:
                    pixmap = QPixmap.fromImage(image).scaled(
                        70,
                        70,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.loaded.emit(pixmap)
        except Exception:
            pass


class InspectionWorker(QThread):
    item_inspected = pyqtSignal(dict)
    finished_inspection = pyqtSignal(int)
    progress_status = pyqtSignal(str)

    def __init__(self, urls: list[str], cookie_path: str | None):
        super().__init__()
        self.urls = urls
        self.cookie_path = cookie_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _get_url_opener(self) -> urllib.request.OpenerDirector:
        if self.cookie_path and os.path.exists(self.cookie_path):
            try:
                cj = http.cookiejar.MozillaCookieJar(self.cookie_path)
                cj.load(ignore_discard=True, ignore_expires=True)
                return urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(cj)
                )
            except Exception:
                pass
        return urllib.request.build_opener()

    def run(self):
        opener = self._get_url_opener()
        found_count = 0

        for idx, raw_url in enumerate(self.urls, 1):
            if self._is_cancelled:
                break

            parsed = parse_instagram_url(raw_url)
            if not parsed:
                continue

            identifier = parsed["identifier"]
            media_id = parsed["media_id"]
            url_type = parsed["type"]
            self.progress_status.emit(
                f"Inspecting [{idx}/{len(self.urls)}]: {identifier}"
            )

            item_data = {
                "url": parsed["clean_url"],
                "shortcode": identifier,
                "uploader": parsed.get("username", "Instagram"),
                "thumb_url": "",
                "media_type": url_type,
                "slides_count": 1,
                "format_options": [],
                "raw_media_items": [],
            }

            api_data = None
            if media_id > 0:
                try:
                    req_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
                    req = urllib.request.Request(
                        req_url,
                        headers={
                            "User-Agent": MOBILE_UA,
                            "X-IG-App-ID": "936619743392459",
                            "Accept": "*/*",
                        },
                    )
                    with opener.open(req, timeout=9) as resp:
                        api_data = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    pass

            if api_data and "items" in api_data and api_data["items"]:
                post = api_data["items"][0]
                user_info = post.get("user", {})
                item_data["uploader"] = user_info.get("username", item_data["uploader"])

                # Case 1: Story / Highlight
                if url_type in ("story", "highlight"):
                    item_data["media_type"] = "story"
                    v_list = post.get("video_versions", [])
                    is_story_video = bool(
                        v_list or post.get("is_video") or post.get("media_type") == 2
                    )
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""

                    if is_story_video:
                        best_v = (
                            max(v_list, key=lambda x: int(x.get("width", 0)))
                            if v_list
                            else {}
                        )
                        vw = best_v.get("width", 1080)
                        vh = best_v.get("height", 1920)
                        item_data["format_options"] = [
                            {
                                "label": f"🎬 Story Video ({vw}x{vh} - Best)",
                                "key": "story_video",
                            },
                            {
                                "label": "🎵 Audio Only (MP3 192kbps)",
                                "key": "audio_mp3",
                            },
                        ]
                        if best_v.get("url"):
                            item_data["raw_media_items"].append(
                                {"url": best_v["url"], "ext": "mp4", "is_video": True}
                            )
                    else:
                        w = cands[0].get("width", 1080) if cands else 1080
                        h = cands[0].get("height", 1920) if cands else 1920
                        item_data["format_options"] = [
                            {
                                "label": f"🖼️ Story Photo ({w}x{h} - Original)",
                                "key": "story_photo",
                            }
                        ]
                        if cands:
                            best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                            item_data["raw_media_items"].append(
                                {"url": best_s["url"], "ext": "jpg", "is_video": False}
                            )

                # Case 2: Carousel อัลบั้ม
                elif "carousel_media" in post and isinstance(
                    post["carousel_media"], list
                ):
                    item_data["media_type"] = "carousel"
                    item_data["slides_count"] = len(post["carousel_media"])
                    first_slide = post["carousel_media"][0]
                    cands = first_slide.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""

                    item_data["format_options"] = [
                        {
                            "label": f"⚡ Best Quality (Photos & Videos) - All {item_data['slides_count']} Items",
                            "key": "best_all",
                        },
                        {
                            "label": "🖼️ Photos Only (Extract images only)",
                            "key": "photos_only",
                        },
                    ]

                    for s in post["carousel_media"]:
                        v_list = s.get("video_versions", [])
                        if v_list or s.get("is_video") or s.get("media_type") == 2:
                            best_v = (
                                max(v_list, key=lambda x: int(x.get("width", 0)))
                                if v_list
                                else {}
                            )
                            if best_v.get("url"):
                                item_data["raw_media_items"].append(
                                    {
                                        "url": best_v["url"],
                                        "ext": "mp4",
                                        "is_video": True,
                                    }
                                )
                        else:
                            s_cands = s.get("image_versions2", {}).get("candidates", [])
                            if s_cands:
                                best_s = max(
                                    s_cands, key=lambda x: int(x.get("width", 0))
                                )
                                item_data["raw_media_items"].append(
                                    {
                                        "url": best_s["url"],
                                        "ext": "jpg",
                                        "is_video": False,
                                    }
                                )

                # Case 3: Video / Reel
                elif (
                    post.get("video_versions")
                    or post.get("is_video", False)
                    or url_type == "video"
                ):
                    item_data["media_type"] = "video"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    v_list = post.get("video_versions", [])
                    best_v = (
                        max(v_list, key=lambda x: int(x.get("width", 0)))
                        if v_list
                        else {}
                    )
                    vw = best_v.get("width", 1080)
                    vh = best_v.get("height", 1920)

                    item_data["format_options"] = [
                        {
                            "label": f"🎬 Best Video ({vw}x{vh} - Max Bitrate)",
                            "key": "video_best",
                        },
                        {"label": "🎞️ H.264 Compatibility Mode", "key": "video_h264"},
                        {"label": "🎵 Audio Only (MP3 192kbps)", "key": "audio_mp3"},
                    ]
                    if best_v.get("url"):
                        item_data["raw_media_items"].append(
                            {"url": best_v["url"], "ext": "mp4", "is_video": True}
                        )

                # Case 4: Single Photo
                else:
                    item_data["media_type"] = "photo"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    w = cands[0].get("width", 1080) if cands else 1080
                    h = cands[0].get("height", 1080) if cands else 1080

                    item_data["format_options"] = [
                        {
                            "label": f"🖼️ Original Resolution ({w}x{h})",
                            "key": "best_single",
                        },
                        {"label": "🖼️ Compressed Web Size", "key": "720p_single"},
                    ]
                    if cands:
                        best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                        item_data["raw_media_items"].append(
                            {"url": best_s["url"], "ext": "jpg", "is_video": False}
                        )

            # Fallback ผ่าน yt-dlp Metadata
            if not item_data["format_options"]:
                try:
                    ydl_opts = {
                        "quiet": True,
                        "logger": SilentLogger(),
                        "http_headers": {
                            "User-Agent": DESKTOP_UA,
                            "X-IG-App-ID": "936619743392459",
                        },
                    }
                    if self.cookie_path and os.path.exists(self.cookie_path):
                        ydl_opts["cookiefile"] = self.cookie_path

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(raw_url, download=False)
                        if info:
                            item_data["uploader"] = info.get(
                                "uploader", item_data["uploader"]
                            )
                            item_data["thumb_url"] = info.get("thumbnail", "")
                            is_vid = info.get("vcodec") != "none" or url_type in (
                                "story",
                                "video",
                            )
                            item_data["media_type"] = (
                                "story"
                                if url_type == "story"
                                else ("video" if is_vid else "photo")
                            )

                            if is_vid:
                                item_data["format_options"] = [
                                    {
                                        "label": "🎬 Best Video (Highest Quality)",
                                        "key": "video_best",
                                    },
                                    {
                                        "label": "🎞️ H.264 Compatibility Mode",
                                        "key": "video_h264",
                                    },
                                    {
                                        "label": "🎵 Audio Only (MP3 192kbps)",
                                        "key": "audio_mp3",
                                    },
                                ]
                            else:
                                item_data["format_options"] = [
                                    {
                                        "label": "🖼️ Best Photo Resolution",
                                        "key": "best_single",
                                    },
                                ]
                except Exception:
                    is_reel = url_type in ("video", "story") or "/reel/" in raw_url
                    item_data["media_type"] = "video" if is_reel else "photo"
                    if is_reel:
                        item_data["format_options"] = [
                            {
                                "label": "🎬 Best Video (Auto-Engine)",
                                "key": "video_best",
                            },
                            {"label": "🎞️ H.264 Mode", "key": "video_h264"},
                            {"label": "🎵 Audio MP3", "key": "audio_mp3"},
                        ]
                    else:
                        item_data["format_options"] = [
                            {
                                "label": "⚡ Best Quality (Auto-Engine)",
                                "key": "best_single",
                            }
                        ]

            self.item_inspected.emit(item_data)
            found_count += 1
            time.sleep(0.2)

        self.finished_inspection.emit(found_count)


class MediaCardWidget(QFrame):
    clicked = pyqtSignal(object, object)
    removed = pyqtSignal(object)

    def __init__(self, data: dict, lang: str = "th"):
        super().__init__()
        self.data = data
        self.lang = lang
        self.is_selected = False
        self.is_completed = False
        self.saved_file_path = None
        self.thumb_loader = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.init_ui()
        self.update_style()
        self.load_thumbnail_async()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.lbl_thumb = QLabel()
        self.lbl_thumb.setFixedSize(70, 70)
        self.lbl_thumb.setStyleSheet("background-color: #141419; border-radius: 5px;")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setText("Loading...")
        layout.addWidget(self.lbl_thumb)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        top_row = QHBoxLayout()
        self.lbl_uploader = QLabel(f"@{self.data.get('uploader', 'Instagram')}")
        self.lbl_uploader.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        top_row.addWidget(self.lbl_uploader)

        m_type = self.data.get("media_type")
        if m_type in ("story", "highlight"):
            badge_text = TRANSLATIONS[self.lang]["badge_story"]
            badge_color = "#9b51e0"
        elif m_type == "carousel":
            badge_text = TRANSLATIONS[self.lang]["badge_carousel"].format(
                count=self.data.get("slides_count", 1)
            )
            badge_color = "#fa7e1e"
        elif m_type == "video":
            badge_text = TRANSLATIONS[self.lang]["badge_video"]
            badge_color = "#d62976"
        else:
            badge_text = TRANSLATIONS[self.lang]["badge_photo"]
            badge_color = "#4a90e2"

        self.lbl_badge = QLabel(f" {badge_text} ")
        self.lbl_badge.setStyleSheet(
            f"background-color: {badge_color}; color: white; border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px 6px;"
        )
        top_row.addWidget(self.lbl_badge)
        top_row.addStretch()
        info_layout.addLayout(top_row)

        self.lbl_sub = QLabel(
            f"ID: {self.data.get('shortcode')} | {self.data.get('url')[:45]}..."
        )
        self.lbl_sub.setStyleSheet("color: #888888; font-size: 10px;")
        info_layout.addWidget(self.lbl_sub)

        bottom_row = QHBoxLayout()
        lbl_q = QLabel(TRANSLATIONS[self.lang]["lbl_quality"])
        lbl_q.setStyleSheet("font-size: 11px; color: #cccccc;")
        bottom_row.addWidget(lbl_q)

        self.cmb_quality = NoScrollComboBox()
        self.cmb_quality.setFixedHeight(26)
        for opt in self.data.get("format_options", []):
            self.cmb_quality.addItem(opt["label"], opt["key"])

        if self.cmb_quality.count() > 0:
            self.cmb_quality.setCurrentIndex(0)

        bottom_row.addWidget(self.cmb_quality, stretch=1)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet(
            "color: #28a745; font-size: 11px; font-weight: bold;"
        )
        bottom_row.addWidget(self.lbl_status)

        self.btn_open_file = QPushButton("📁 Open")
        self.btn_open_file.setFixedHeight(24)
        self.btn_open_file.setStyleSheet(
            "background-color: #28a745; font-size: 10px; padding: 2px 8px;"
        )
        self.btn_open_file.setVisible(False)
        self.btn_open_file.clicked.connect(self.open_downloaded_file)
        bottom_row.addWidget(self.btn_open_file)

        self.btn_delete = QPushButton("✕")
        self.btn_delete.setFixedSize(24, 24)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setStyleSheet(
            "background-color: #3d1c24; color: #ff4d6d; border-radius: 12px; font-weight: bold;"
        )
        self.btn_delete.clicked.connect(self.cleanup_and_delete)
        bottom_row.addWidget(self.btn_delete)

        info_layout.addLayout(bottom_row)
        layout.addLayout(info_layout, stretch=1)

    def mousePressEvent(self, event):
        self.clicked.emit(self, event)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_style()

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet(
                """
                MediaCardWidget {
                    background-color: #2b1f2d;
                    border: 2px solid #d62976;
                    border-radius: 8px;
                }
                QLabel { color: #ffffff; }
                QComboBox {
                    background-color: #17171e;
                    border: 1px solid #d62976;
                    border-radius: 4px;
                    padding: 4px 8px;
                    color: #ffffff;
                    font-size: 11px;
                }
            """
            )
        else:
            self.setStyleSheet(
                """
                MediaCardWidget {
                    background-color: #21212b;
                    border: 1px solid #363647;
                    border-radius: 8px;
                }
                MediaCardWidget:hover {
                    border: 1px solid #5a5a72;
                }
                QLabel { color: #eaeaea; }
                QComboBox {
                    background-color: #17171e;
                    border: 1px solid #4a4a5e;
                    border-radius: 4px;
                    padding: 4px 8px;
                    color: #ffffff;
                    font-size: 11px;
                }
            """
            )

    def mark_completed(self, file_path: str):
        self.is_completed = True
        self.saved_file_path = file_path
        self.lbl_status.setText("✔ Done")
        self.lbl_status.setStyleSheet(
            "color: #28a745; font-size: 11px; font-weight: bold;"
        )
        self.btn_open_file.setVisible(True)

    def open_downloaded_file(self):
        if self.saved_file_path and os.path.exists(self.saved_file_path):
            if sys.platform == "win32":
                os.startfile(os.path.normpath(self.saved_file_path))
            else:
                subprocess.Popen(["xdg-open", self.saved_file_path])

    def load_thumbnail_async(self):
        thumb_url = self.data.get("thumb_url")
        if not thumb_url:
            self.lbl_thumb.setText("No Preview")
            return

        self.thumb_loader = ThumbnailLoader(thumb_url)
        self.thumb_loader.loaded.connect(self._on_thumbnail_loaded)
        self.thumb_loader.start()

    def _on_thumbnail_loaded(self, pixmap: QPixmap):
        try:
            self.lbl_thumb.setPixmap(pixmap)
        except Exception:
            pass

    def cleanup_and_delete(self):
        if self.thumb_loader and self.thumb_loader.isRunning():
            self.thumb_loader.cancel()
            self.thumb_loader.wait(300)
        self.removed.emit(self)

    def get_selected_format(self) -> str:
        return self.cmb_quality.currentData()


class GridDownloadWorker(QThread):
    progress_signal = pyqtSignal(dict)
    item_started = pyqtSignal(int, int, str)
    item_finished = pyqtSignal(int, bool, str, str)
    all_finished = pyqtSignal(int, int, bool)

    def __init__(
        self, card_data_list: list[dict], save_path: str, cookie_path: str | None
    ):
        super().__init__()
        self.items = card_data_list
        self.save_path = save_path
        self.cookie_path = cookie_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _download_file(self, url: str, dest_path: str) -> bool:
        """ดาวน์โหลดไฟล์แบบ Chunked พร้อมส่ง Progress Bar Signal อย่างต่อเนื่อง"""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": DESKTOP_UA,
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp, open(
                dest_path, "wb"
            ) as f:
                total_bytes = int(resp.info().get("Content-Length", 0))
                downloaded_bytes = 0
                block_size = 64 * 1024
                start_time = time.time()

                while True:
                    if self._is_cancelled:
                        return False
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    f.write(chunk)

                    elapsed = time.time() - start_time
                    speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0
                    pct = (
                        (downloaded_bytes / total_bytes * 100)
                        if total_bytes > 0
                        else 100.0
                    )

                    self.progress_signal.emit(
                        {
                            "percent": pct,
                            "downloaded": downloaded_bytes,
                            "total": total_bytes,
                            "speed": speed,
                            "eta": (
                                ((total_bytes - downloaded_bytes) / speed)
                                if speed > 0 and total_bytes > downloaded_bytes
                                else 0
                            ),
                        }
                    )
            return True
        except Exception:
            return False

    def run(self):
        os.makedirs(self.save_path, exist_ok=True)
        ffmpeg_dir = get_ffmpeg_dir()
        total = len(self.items)
        success_count = 0
        fail_count = 0

        def progress_hook(d):
            if self._is_cancelled:
                raise Exception("Cancelled")
            if d.get("status") == "downloading":
                total_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                down_b = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                pct = (down_b / total_b * 100) if total_b else 0.0
                self.progress_signal.emit(
                    {
                        "percent": pct,
                        "downloaded": down_b,
                        "total": total_b,
                        "speed": speed,
                        "eta": eta,
                    }
                )

        for idx, item in enumerate(self.items, 1):
            if self._is_cancelled:
                break

            shortcode = sanitize_filename(item["shortcode"])
            uploader = sanitize_filename(item.get("uploader", "Instagram"))
            url = item["url"]
            chosen_format = item["selected_format"]
            self.item_started.emit(idx - 1, total, shortcode)

            ok = False
            current_date = time.strftime("%Y%m%d")
            final_saved_path = self.save_path

            # 1. Direct Media Items (Carousels, Photos, Stories)
            # รองรับ Format Keys ทุกตัวของ Direct Extraction
            direct_format_keys = (
                "best_all",
                "photos_only",
                "best_single",
                "720p_single",
                "story_video",
                "story_photo",
                "1080p_all",
                "auto_best",
            )

            if item.get("raw_media_items") and chosen_format in direct_format_keys:
                img_list = item["raw_media_items"]
                if chosen_format == "photos_only":
                    img_list = [m for m in img_list if not m.get("is_video")]

                success_slides = 0
                for s_idx, raw_media in enumerate(img_list, 1):
                    if self._is_cancelled:
                        break

                    ext = raw_media.get("ext", "jpg")
                    if len(img_list) > 1:
                        fn = f"{uploader}_{current_date}_{shortcode}_{s_idx:02d}.{ext}"
                    else:
                        fn = f"{uploader}_{current_date}_{shortcode}.{ext}"

                    fp = os.path.join(self.save_path, fn)
                    if self._download_file(raw_media["url"], fp):
                        success_slides += 1
                        final_saved_path = fp

                ok = success_slides > 0

            # 2. yt-dlp Engine (Videos / Reels / Audio MP3 / Fallback)
            if not ok and not self._is_cancelled:
                ydl_opts = {
                    "paths": {"home": self.save_path},
                    "windowsfilenames": True,
                    "trim_file_name": 120,
                    "http_headers": {
                        "User-Agent": DESKTOP_UA,
                        "X-IG-App-ID": "936619743392459",
                    },
                    "outtmpl": "%(uploader,uploader_id|Instagram)s_%(upload_date>%Y%m%d,Unknown)s_%(id)s.%(ext)s",
                    "progress_hooks": [progress_hook],
                    "logger": SilentLogger(),
                    "quiet": True,
                    "merge_output_format": "mp4",
                }
                if ffmpeg_dir:
                    ydl_opts["ffmpeg_location"] = ffmpeg_dir
                if self.cookie_path and os.path.exists(self.cookie_path):
                    ydl_opts["cookiefile"] = self.cookie_path

                if chosen_format == "audio_mp3":
                    ydl_opts["format"] = "best"
                    ydl_opts["postprocessors"] = [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ]
                elif chosen_format == "video_h264":
                    ydl_opts["format"] = "bestvideo*+bestaudio/best"
                    ydl_opts["format_sort"] = ["vcodec:h264", "res", "fps"]
                else:
                    ydl_opts["format"] = "bestvideo*+bestaudio/best"
                    ydl_opts["format_sort"] = ["res:1080", "res", "vbr", "fps", "size"]

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            final_saved_path = ydl.prepare_filename(info)
                    ok = True
                except Exception:
                    ok = False

            if ok:
                success_count += 1
                self.item_finished.emit(idx - 1, True, "Done", final_saved_path)
            else:
                if not self._is_cancelled:
                    fail_count += 1
                    self.item_finished.emit(idx - 1, False, "Failed", "")

            time.sleep(0.3)

        self.all_finished.emit(success_count, fail_count, self._is_cancelled)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(820, 760)

        icon_file = get_icon_path()
        if os.path.exists(icon_file):
            self.setWindowIcon(QIcon(icon_file))

        self.settings = QSettings("MySoftware", "InstagramProDownloader")
        self.inspect_worker = None
        self.download_worker = None
        self.cards: list[MediaCardWidget] = []
        self.anchor_card_idx = None
        self.last_clipboard_text = ""

        system_lang = "th" if QLocale.system().name().startswith("th") else "en"
        self.current_lang = self.settings.value("language", system_lang)

        self.load_paths()
        self.init_ui()
        self.apply_dark_theme()
        self.setup_clipboard_monitor()
        self.load_saved_settings()
        self.retranslate_ui()

    def t(self, key: str) -> str:
        return TRANSLATIONS.get(self.current_lang, {}).get(key, key)

    def load_paths(self):
        default_dl = os.path.join(
            os.path.expanduser("~"), "Downloads", "InstagramDownloads"
        )
        self.save_dir = str(self.settings.value("save_dir", default_dl))
        app_data_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), "InstagramProDownloader"
        )
        os.makedirs(app_data_dir, exist_ok=True)
        self.cookie_path = os.path.join(app_data_dir, "cookies.txt")

    def init_ui(self):
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
        self.btn_select_all.setStyleSheet(
            "background-color: #2e2e3d; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_select_all.clicked.connect(self.select_all_cards)
        sel_toolbar.addWidget(self.btn_select_all)

        self.btn_delete_selected = QPushButton()
        self.btn_delete_selected.setFixedHeight(24)
        self.btn_delete_selected.setStyleSheet(
            "background-color: #8b2635; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_delete_selected.clicked.connect(self.delete_selected_cards)
        sel_toolbar.addWidget(self.btn_delete_selected)

        self.btn_clear_completed = QPushButton()
        self.btn_clear_completed.setFixedHeight(24)
        self.btn_clear_completed.setStyleSheet(
            "background-color: #3b3b4f; font-size: 11px; padding: 2px 10px;"
        )
        self.btn_clear_completed.clicked.connect(self.clear_completed_cards)
        sel_toolbar.addWidget(self.btn_clear_completed)

        sel_toolbar.addStretch()
        self.lbl_selection_count = QLabel("เลือกอยู่: 0 / 0 รายการ")
        self.lbl_selection_count.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        sel_toolbar.addWidget(self.lbl_selection_count)
        grid_group_layout.addLayout(sel_toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(
            "background-color: #17171e; border: none; border-radius: 6px;"
        )

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

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.SelectAll) or (
            event.key() == Qt.Key.Key_A
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
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

    def on_card_clicked(self, card: MediaCardWidget, event):
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

    def select_all_cards(self):
        for card in self.cards:
            card.set_selected(True)
        self.update_selection_ui()

    def deselect_all_cards(self):
        for card in self.cards:
            card.set_selected(False)
        self.anchor_card_idx = None
        self.update_selection_ui()

    def delete_selected_cards(self):
        selected_cards = [c for c in self.cards if c.is_selected]
        for card in selected_cards:
            self.remove_card(card)
        self.update_selection_ui()

    def clear_completed_cards(self):
        completed_cards = [c for c in self.cards if c.is_completed]
        for card in completed_cards:
            self.remove_card(card)
        self.update_selection_ui()

    def update_selection_ui(self):
        selected_count = sum(1 for c in self.cards if c.is_selected)
        total_count = len(self.cards)
        self.lbl_selection_count.setText(
            self.t("lbl_selection_format").format(
                selected=selected_count, total=total_count
            )
        )

    def update_cookie_status_ui(self):
        has_cookie = os.path.exists(self.cookie_path)
        if has_cookie:
            self.lbl_cookie_status.setText(self.t("cookie_connected"))
            self.lbl_cookie_status.setStyleSheet("color: #28a745; font-size: 11px;")
            self.btn_clear_cookie.setEnabled(True)
        else:
            self.lbl_cookie_status.setText(self.t("cookie_none"))
            self.lbl_cookie_status.setStyleSheet("color: #888888; font-size: 11px;")
            self.btn_clear_cookie.setEnabled(False)

    def import_cookie_file(self):
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

    def clear_cookie_file(self):
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

    def on_language_changed(self):
        self.current_lang = self.cmb_lang.currentData()
        self.settings.setValue("language", self.current_lang)
        self.retranslate_ui()

    def retranslate_ui(self):
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

    def apply_dark_theme(self):
        self.setStyleSheet(
            """
            QWidget {
                background-color: #16161c;
                color: #eaeaea;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #2e2e3d;
                border-radius: 6px;
                margin-top: 8px;
                font-weight: bold;
                color: #fa7e1e;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPlainTextEdit {
                background-color: #20202a;
                border: 1px solid #38384a;
                border-radius: 5px;
                padding: 5px;
                color: #ffffff;
            }
            QPushButton {
                background-color: #d62976;
                border: none;
                border-radius: 5px;
                color: #ffffff;
                font-weight: bold;
                padding: 5px 12px;
            }
            QPushButton:hover { background-color: #fa7e1e; }
            QPushButton:disabled { background-color: #2c2c38; color: #606070; }
            QProgressBar {
                background-color: #20202a;
                border: 1px solid #38384a;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
                font-size: 9px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #fa7e1e, stop:1 #d62976);
                border-radius: 3px;
            }
            QCheckBox { color: #cccccc; }
        """
        )

    def load_saved_settings(self):
        self.chk_clipboard.setChecked(
            self.settings.value("auto_clipboard", True, type=bool)
        )

    def setup_clipboard_monitor(self):
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_change)

    def on_clipboard_change(self):
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

    def start_inspection(self):
        raw_text = self.txt_urls.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Warning", self.t("warn_no_url"))
            return

        # กวาดหา URL ของ Instagram ทั้งหมดในช่องข้อความ
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
            QMessageBox.information(
                self, "Cookie Required", self.t("warn_story_need_cookie")
            )

        self.btn_inspect.setEnabled(False)
        self.btn_download_all.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_status.setText(self.t("status_inspecting"))

        self.inspect_worker = InspectionWorker(valid_urls, self.cookie_path)
        self.inspect_worker.item_inspected.connect(self.add_card)
        self.inspect_worker.progress_status.connect(
            lambda msg: self.lbl_status.setText(msg)
        )
        self.inspect_worker.finished_inspection.connect(self.on_inspection_finished)
        self.inspect_worker.start()

    def add_card(self, data: dict):
        card = MediaCardWidget(data, self.current_lang)
        card.clicked.connect(self.on_card_clicked)
        card.removed.connect(self.remove_card)
        self.cards.append(card)
        self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.update_selection_ui()

    def remove_card(self, card: MediaCardWidget):
        if card in self.cards:
            if card.thumb_loader and card.thumb_loader.isRunning():
                card.thumb_loader.cancel()
                card.thumb_loader.wait(300)
            self.cards.remove(card)
            self.cards_layout.removeWidget(card)
            card.deleteLater()
            self.update_selection_ui()

    def on_inspection_finished(self, count: int):
        self.btn_inspect.setEnabled(True)
        self.btn_download_all.setEnabled(len(self.cards) > 0)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText(self.t("inspect_done").format(count=len(self.cards)))
        self.txt_urls.clear()
        self.update_selection_ui()

    def start_download_all(self):
        if not self.cards:
            return

        has_audio = any(
            card.get_selected_format() == "audio_mp3" for card in self.cards
        )
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

        self.download_worker = GridDownloadWorker(
            download_list, self.save_dir, self.cookie_path
        )
        self.download_worker.item_started.connect(self.on_item_download_started)
        self.download_worker.item_finished.connect(self.on_item_download_finished)
        self.download_worker.progress_signal.connect(self.update_progress)
        self.download_worker.all_finished.connect(self.on_all_downloads_finished)
        self.download_worker.start()

    def on_item_download_started(self, card_idx: int, total: int, shortcode: str):
        if card_idx < len(self.cards):
            self.cards[card_idx].lbl_status.setText("Downloading...")
            self.cards[card_idx].lbl_status.setStyleSheet(
                "color: #fa7e1e; font-size: 11px;"
            )
        self.lbl_status.setText(f"Downloading [{card_idx+1}/{total}]: {shortcode}")

    def on_item_download_finished(
        self, card_idx: int, ok: bool, text: str, saved_path: str
    ):
        if card_idx < len(self.cards):
            if ok:
                self.cards[card_idx].mark_completed(saved_path)
            else:
                self.cards[card_idx].lbl_status.setText("✖ Failed")
                self.cards[card_idx].lbl_status.setStyleSheet(
                    "color: #dc3545; font-size: 11px;"
                )

    def update_progress(self, d: dict):
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

    def on_all_downloads_finished(self, success: int, fail: int, is_cancelled: bool):
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

    def cancel_operation(self):
        if self.inspect_worker and self.inspect_worker.isRunning():
            self.inspect_worker.cancel()
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
        self.lbl_status.setText(self.t("status_cancelled"))
        self.btn_cancel.setEnabled(False)

    def browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Folder", self.save_dir)
        if chosen:
            self.save_dir = chosen
            self.lbl_path.setText(f"{self.t('save_path_prefix')}{self.save_dir}")

    def open_folder(self):
        os.makedirs(self.save_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(os.path.normpath(self.save_dir))
        else:
            subprocess.Popen(["xdg-open", self.save_dir])

    def closeEvent(self, event: QCloseEvent):
        # ตัด Signal ทั้งหมดอย่างปลอดภัยเพื่อป้องกัน Signal Fire ไปยัง Widget ที่กำลังปิด
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
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mycompany.instagram.inspector.v1"
        )
    except Exception:
        pass

    app = QApplication(sys.argv)
    icon_file = get_icon_path()
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
