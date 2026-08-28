import re
from typing import Dict, Any, List, Tuple

# Application Metadata
APP_USER_MODEL_ID = "xxdadixx.instagramprodownloader.app.1.0"
APP_NAME = "Instagram Pro Downloader - Studio Inspector"
APP_VERSION = "1.0.0"

# Instagram Application IDs
IG_APP_ID = "936619743392459"
IG_WEB_APP_ID = "936619743392459"

# User-Agent Definitions & Backward Compatibility Aliases
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_USER_AGENT = DESKTOP_UA
USER_AGENT = DESKTOP_UA
UA_DESKTOP = DESKTOP_UA

MOBILE_UA = "Instagram 315.0.0.38.109 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991N; o1s; exynos2100; en_US; 564998762)"
MOBILE_USER_AGENT = MOBILE_UA
UA_MOBILE = MOBILE_UA

# Default HTTP Headers
DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": DESKTOP_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-IG-App-ID": IG_APP_ID,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# URL Parsing Regular Expressions
REELS_TAB_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?instagram\.com\/([a-zA-Z0-9_\.]+)\/reels\/?(?:\?.*)?$",
    re.IGNORECASE,
)
POST_REEL_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?instagram\.com\/(?:p|reel|tv)\/([a-zA-Z0-9_\-]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)
STORIES_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?instagram\.com\/stories\/([a-zA-Z0-9_\.]+)(?:\/(\d+))?\/?(?:\?.*)?$",
    re.IGNORECASE,
)
HIGHLIGHTS_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?instagram\.com\/stories\/highlights\/([a-zA-Z0-9_\-]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)
PROFILE_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?instagram\.com\/([a-zA-Z0-9_\.]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)

RESERVED_USERNAMES = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "direct",
    "accounts",
    "developer",
    "about",
    "legal",
    "api",
    "graphql",
    "support",
    "press",
}

QUALITY_PRESETS: List[Tuple[str, str]] = [
    ("best_video", "Best Video (Highest Quality)"),
    ("1080p", "1080p Full HD"),
    ("720p", "720p HD"),
    ("audio_only", "Audio Only (MP3/M4A)"),
]
