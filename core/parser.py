import re
from urllib.parse import urlparse, urlunparse
from typing import Dict, Any, Optional

from config.constants import (
    REELS_TAB_REGEX,
    POST_REEL_REGEX,
    STORIES_REGEX,
    HIGHLIGHTS_REGEX,
    PROFILE_REGEX,
    RESERVED_USERNAMES,
)


def normalize_url(url: str) -> str:
    """
    Cleans, normalizes, and ensures protocol and canonical host formatting for Instagram URLs.
    """
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    if (
        "instagram.com" in netloc
        or "ddinstagram.com" in netloc
        or "kkinstagram.com" in netloc
        or "instagr.am" in netloc
    ):
        netloc = "www.instagram.com"

    path = parsed.path
    if not path.endswith("/") and not path.endswith((".jpg", ".png", ".mp4")):
        path += "/"

    clean_url = urlunparse((parsed.scheme, netloc, path, "", "", ""))
    return clean_url


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Decomposes an Instagram URL into its corresponding entity type and identifiers.
    """
    if not url:
        return {"type": "unknown", "valid": False, "raw_url": url}

    clean_url = normalize_url(url)

    # 1. Profile Reels Tab
    reels_match = REELS_TAB_REGEX.match(clean_url)
    if reels_match:
        username = reels_match.group(1).lower()
        if username not in RESERVED_USERNAMES:
            return {
                "type": "profile_reels",
                "valid": True,
                "username": username,
                "shortcode": None,
                "target_id": None,
                "clean_url": clean_url,
                "raw_url": url,
            }

    # 2. Single Post / Reel / TV
    post_match = POST_REEL_REGEX.match(clean_url)
    if post_match:
        shortcode = post_match.group(1)
        url_lower = clean_url.lower()
        if "/reel/" in url_lower:
            media_type = "reel"
        elif "/tv/" in url_lower:
            media_type = "tv"
        else:
            media_type = "post"

        return {
            "type": media_type,
            "valid": True,
            "username": None,
            "shortcode": shortcode,
            "target_id": None,
            "clean_url": clean_url,
            "raw_url": url,
        }

    # 3. Highlights
    highlight_match = HIGHLIGHTS_REGEX.match(clean_url)
    if highlight_match:
        highlight_id = highlight_match.group(1)
        return {
            "type": "highlight",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": highlight_id,
            "clean_url": clean_url,
            "raw_url": url,
        }

    # 4. Stories
    story_match = STORIES_REGEX.match(clean_url)
    if story_match:
        username = story_match.group(1).lower()
        story_id = story_match.group(2)
        if username not in RESERVED_USERNAMES:
            return {
                "type": "story",
                "valid": True,
                "username": username,
                "shortcode": None,
                "target_id": story_id,
                "clean_url": clean_url,
                "raw_url": url,
            }

    # 5. Base User Profile
    profile_match = PROFILE_REGEX.match(clean_url)
    if profile_match:
        username = profile_match.group(1).lower()
        if username not in RESERVED_USERNAMES:
            return {
                "type": "profile",
                "valid": True,
                "username": username,
                "shortcode": None,
                "target_id": None,
                "clean_url": clean_url,
                "raw_url": url,
            }

    return {
        "type": "unknown",
        "valid": False,
        "username": None,
        "shortcode": None,
        "target_id": None,
        "clean_url": clean_url,
        "raw_url": url,
    }
