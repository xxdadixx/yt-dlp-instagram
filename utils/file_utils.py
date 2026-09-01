"""
utils/file_utils.py - Path resolvers, standard OS application data paths,
filename sanitization, and system binary discovery.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


def sanitize_filesystem_name(name: str, max_length: int = 120) -> str:
    """Sanitizes strings to prevent path traversal and illegal OS characters."""
    if not name:
        return "unnamed_media"

    cleaned = str(name).replace("../", "").replace("..\\", "")
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", cleaned)
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned).strip(" .")

    if not cleaned:
        cleaned = "unnamed_media"

    return cleaned[:max_length]


def get_app_dir() -> str:
    """Returns the immutable application directory (bundle or script root)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_resource_dir() -> str:
    """Returns the unpacked resource directory for PyInstaller (_MEIPASS)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", get_app_dir())
    return get_app_dir()


def get_user_data_dir(app_name: str = "InstagramProDownloader") -> str:
    """
    Resolves the standard cross-platform writable user configuration and data directory:
    - Windows: %APPDATA%/InstagramProDownloader
    - macOS: ~/Library/Application Support/InstagramProDownloader
    - Linux: ~/.config/InstagramProDownloader (or $XDG_CONFIG_HOME)
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

    data_dir = os.path.join(base, app_name)
    os.makedirs(data_dir, exist_ok=True)
    return os.path.abspath(data_dir)


def get_icon_path() -> str:
    """Resolves the valid path to the application icon."""
    res_icon = os.path.join(get_resource_dir(), "app.ico")
    if os.path.exists(res_icon):
        return res_icon
    return os.path.join(get_app_dir(), "app.ico")


def get_ffmpeg_dir() -> str | None:
    """Discovers the directory containing the ffmpeg executable."""
    candidates = [
        get_resource_dir(),
        get_app_dir(),
        os.path.join(get_app_dir(), "ffmpeg"),
        os.path.join(get_app_dir(), "ffmpeg", "bin"),
        os.path.join(get_user_data_dir(), "bin"),
    ]
    for folder in candidates:
        exe_path = os.path.join(
            folder, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        )
        if os.path.isfile(exe_path) and os.access(exe_path, os.X_OK):
            return folder
    system_ffmpeg = shutil.which("ffmpeg")
    return os.path.dirname(system_ffmpeg) if system_ffmpeg else None
