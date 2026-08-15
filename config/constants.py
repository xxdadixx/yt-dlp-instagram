"""
config/constants.py - Global constants and configuration values.
"""

INSTAGRAM_URL_REGEX = (
    r"https?://(?:www\.)?instagram\.com/"
    r"(?:stories/[A-Za-z0-9_\-\.]+/[0-9]+|"
    r"stories/highlights/[0-9]+|"
    r"reel/[A-Za-z0-9_\-\.]+|"
    r"reels/[A-Za-z0-9_\-\.]+|"
    r"p/[A-Za-z0-9_\-\.]+|"
    r"tv/[A-Za-z0-9_\-\.]+)/?"
)

MOBILE_UA = (
    "Instagram 278.0.0.19.115 Android "
    "(30/11; 480dpi; 1080x2176; samsung; SM-G991B; o1s; exynos2100; en_US; 458364234)"
)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

IG_APP_ID = "936619743392459"
APP_USER_MODEL_ID = "mycompany.instagram.inspector.v1"
ORGANIZATION_NAME = "MySoftware"
APPLICATION_NAME = "InstagramProDownloader"