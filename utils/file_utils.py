"""
utils/file_utils.py - Path resolvers, filename sanitization, and system binaries detection.
"""

import os
import re
import shutil
import sys


def sanitize_filesystem_name(name: str, max_length: int = 120) -> str:
    """
    Sanitizes string components to prevent path traversal and illegal OS characters.
    """
    if not name:
        return "unnamed_media"

    # 1. Remove directory traversal sequences
    cleaned = str(name).replace("../", "").replace("..\\", "")

    # 2. Strip illegal Windows / POSIX file characters
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", cleaned)

    # 3. Strip non-printable control characters and boundary whitespace/periods
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned).strip(" .")

    if not cleaned:
        cleaned = "unnamed_media"

    return cleaned[:max_length]


def get_app_dir() -> str:
    """ค้นหา Base directory ของ Application ทั้งแบบ Script และ Frozen (PyInstaller)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_resource_dir() -> str:
    """ค้นหา Resource directory สำหรับ PyInstaller temporary unpack path (_MEIPASS)"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", get_app_dir())
    return get_app_dir()


def get_icon_path() -> str:
    """ระบุ Path ของไฟล์ app.ico อย่างถูกต้อง"""
    res_icon = os.path.join(get_resource_dir(), "app.ico")
    if os.path.exists(res_icon):
        return res_icon
    return os.path.join(get_app_dir(), "app.ico")


def get_ffmpeg_dir() -> str | None:
    """ตรวจสอบและหาตำแหน่งโฟลเดอร์ของ ffmpeg.exe ในระบบ"""
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
