"""
config/constants.py - Application Constants, Identifiers, Headers, and Regex Patterns
"""

import os
import re
from pathlib import Path

# ==========================================
# Windows AppUserModelID & App Metadata
# ==========================================
APP_USER_MODEL_ID = "xxdadixx.instagramprodownloader.studioinspector.1.0.0"
APP_NAME = "Instagram Pro Downloader - Studio Inspector"
APP_VERSION = "1.0.0"

# ==========================================
# Instagram API Constants & Headers
# ==========================================
IG_APP_ID = "936619743392459"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

MOBILE_USER_AGENT = (
    "Instagram 300.0.0.29.110 Android (33/13; 420dpi; 1080x2400; "
    "Google/google; Pixel 7; cheetah; cheetah; en_US; 515904797)"
)

# ==========================================
# URL Decomposition Regex Patterns
# ==========================================
RE_PROFILE_REELS = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_\.]+)/reels/?(?:\?.*)?$",
    re.IGNORECASE,
)
RE_REEL = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/reel/([a-zA-Z0-9_-]+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
RE_POST = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/p/([a-zA-Z0-9_-]+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
RE_STORIES = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/stories/([a-zA-Z0-9_\.]+)(?:/(\d+))?/?(?:\?.*)?$",
    re.IGNORECASE,
)
RE_HIGHLIGHTS = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/stories/highlights/(\d+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
RE_PROFILE = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_\.]+)/?(?:\?.*)?$",
    re.IGNORECASE,
)

# ==========================================
# Preset Configurations
# ==========================================
FORMAT_BEST_VIDEO = "Best Video (Highest Quality)"
FORMAT_BEST_AUDIO = "Best Audio (MP3/M4A)"
FORMAT_THUMBNAIL_ONLY = "Thumbnail / Cover Image Only"

QUALITY_PRESETS = [
    FORMAT_BEST_VIDEO,
    FORMAT_BEST_AUDIO,
    FORMAT_THUMBNAIL_ONLY,
]

DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "Instagram")
