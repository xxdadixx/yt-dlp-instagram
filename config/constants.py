"""
config/constants.py - Application configuration, API URLs, and Instagram constants.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

APP_USER_MODEL_ID = "xxdadixx.instagramprodownloader.app.1.0"
APP_NAME = "Instagram Pro Downloader - Studio Inspector"
APP_VERSION = "2.4.1"

# Instagram Client Headers & Identifiers
IG_APP_ID = "936619743392459"
IG_WEB_APP_ID = "936619743392459"
IG_WWW_CLAIM = "0"

# User Agents
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
WEB_USER_AGENT = DEFAULT_USER_AGENT

MOBILE_USER_AGENT = (
    "Instagram 315.0.0.38.109 Android (33/13; 420dpi; 1080x2400; "
    "samsung; SM-G991N; o1s; exynos2100; en_US; 564998762)"
)

# Base URLs & Endpoints
IG_BASE_URL = "https://www.instagram.com"
IG_API_BASE_URL = "https://i.instagram.com/api/v1"
IG_CLIPS_USER_URL = "https://i.instagram.com/api/v1/clips/user/"
IG_FEED_USER_URL = "https://i.instagram.com/api/v1/feed/user/{user_id}/"
IG_WEB_PROFILE_INFO_URL = (
    "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
)
IG_USER_INFO_MOBILE_URL = (
    "https://i.instagram.com/api/v1/users/{username}/usernameinfo/"
)
IG_USER_LOOKUP_URL = "https://i.instagram.com/api/v1/users/lookup/"
IG_WEB_SEARCH_URL = "https://www.instagram.com/web/search/topsearch/?context=blended&query={username}&rank_token=0.5"
IG_USERS_SEARCH_URL = "https://i.instagram.com/api/v1/users/search/?q={username}"
IG_WEB_PROFILE_ALT_URL = "https://www.instagram.com/{username}/?__a=1&__d=dis"
INSTAGRAM_DOMAINS: Tuple[str, ...] = (
    "instagram.com",
    "ddinstagram.com",
    "kkinstagram.com",
    "instagr.am",
)

# HTTP Headers
DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-IG-App-ID": IG_APP_ID,
    "X-IG-WWW-Claim": IG_WWW_CLAIM,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

DEFAULT_MOBILE_HEADERS: Dict[str, str] = {
    "User-Agent": MOBILE_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-IG-App-ID": IG_APP_ID,
    "X-IG-WWW-Claim": IG_WWW_CLAIM,
}

# Instagram Media Types
MEDIA_TYPE_PHOTO: int = 1
MEDIA_TYPE_VIDEO: int = 2
MEDIA_TYPE_CAROUSEL: int = 8

# Crawler & Pagination Limits
DEFAULT_PAGE_SIZE: int = 50
MAX_PAGINATION_PAGES: int = 50
REQUEST_DELAY_SECONDS: float = 0.5
DEFAULT_REQUEST_TIMEOUT: int = 15

# URL Regular Expressions
REELS_TAB_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am)\/([a-zA-Z0-9_\.]+)\/reels\/?(?:\?.*)?$",
    re.IGNORECASE,
)
POST_REEL_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am)\/(?:share\/)?(?:p|reel|reels|tv)\/([a-zA-Z0-9_\-]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)
STORIES_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com)\/stories\/([a-zA-Z0-9_\.]+)(?:\/(\d+))?\/?(?:\?.*)?$",
    re.IGNORECASE,
)
HIGHLIGHTS_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com)\/(?:stories\/highlights\/([a-zA-Z0-9_\-]+)|s\/([a-zA-Z0-9_\-]+))\/?(?:\?.*)?$",
    re.IGNORECASE,
)
AUDIO_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com)\/(?:reels\/audio|audio)\/([a-zA-Z0-9_\-]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)
PROFILE_REGEX = re.compile(
    r"^https?:\/\/(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am)\/([a-zA-Z0-9_\.]+)\/?(?:\?.*)?$",
    re.IGNORECASE,
)

# Reserved Instagram URL Path Usernames
RESERVED_USERNAMES = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "highlights",
    "audio",
    "share",
    "explore",
    "tags",
    "direct",
    "accounts",
    "developer",
    "about",
    "legal",
    "api",
    "graphql",
    "support",
    "press",
    "terms",
    "privacy",
}

# Quality Options for Inspector / Downloader
QUALITY_PRESETS: List[Tuple[str, str]] = [
    ("best_video", "Best Video (Highest Quality)"),
    ("1080p", "1080p Full HD"),
    ("720p", "720p HD"),
    ("audio_only", "Audio Only (MP3/M4A)"),
]
