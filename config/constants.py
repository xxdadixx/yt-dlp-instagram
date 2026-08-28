"""
config/constants.py - Global constants, headers, and endpoints for yt-dlp-instagram.
"""

import os
from pathlib import Path

# Paths
APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOWNLOAD_DIR = os.path.join(APP_ROOT, "downloads")
DEFAULT_SESSION_DIR = os.path.join(APP_ROOT, "session")

# Headers & Tokens
APP_ID = "936619743392459"
DESKTOP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
MOBILE_USER_AGENT = "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; Xiaomi; 2201116PG; veux; qcom; en_US; 454316182)"

# API Endpoints
GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
GRAPHQL_REELS_QUERY_HASH = "bc78b1f868b0f4439c2c62c2f6d538e1"
WEB_PROFILE_INFO_URL = "https://i.instagram.com/api/v1/users/web_profile_info/"
CLIPS_API_URL = "https://i.instagram.com/api/v1/clips/user/"
USER_FEED_API_URL = "https://i.instagram.com/api/v1/feed/user/{user_id}/"

# Regex URL Patterns
IG_REELS_TAB_PATTERN = (
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/reels/?"
)
IG_POST_PATTERN = (
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)/?"
)
IG_STORIES_PATTERN = (
    r"(?:https?://)?(?:www\.)?instagram\.com/stories/([A-Za-z0-9_.]+)/(\d+)/?"
)
IG_HIGHLIGHT_PATTERN = (
    r"(?:https?://)?(?:www\.)?instagram\.com/stories/highlights/(\d+)/?"
)
IG_PROFILE_PATTERN = r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?"
