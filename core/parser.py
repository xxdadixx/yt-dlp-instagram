"""
core/parser.py - URL parsing, data normalization, media ID encoding, and standalone video filtering.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from config.constants import (
    HIGHLIGHTS_REGEX,
    MEDIA_TYPE_CAROUSEL,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    POST_REEL_REGEX,
    PROFILE_REGEX,
    REELS_TAB_REGEX,
    RESERVED_USERNAMES,
    STORIES_REGEX,
)

IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def id_to_shortcode(media_id: int) -> str:
    """Converts a numeric Instagram media ID (pk) to a base64-style shortcode."""
    if not media_id or media_id < 0:
        return ""
    shortcode_chars = []
    while media_id > 0:
        remainder = media_id % 64
        shortcode_chars.append(IG_ALPHABET[remainder])
        media_id //= 64
    return "".join(reversed(shortcode_chars))


def shortcode_to_id(shortcode: str) -> int:
    """Converts an Instagram shortcode back to a numeric media ID (pk)."""
    if not shortcode:
        return 0
    media_id = 0
    for char in shortcode:
        idx = IG_ALPHABET.find(char)
        if idx == -1:
            continue
        media_id = media_id * 64 + idx
    return media_id


def is_standalone_video(item: Dict[str, Any]) -> bool:
    """
    Checks if the media item represents a standalone video reel.
    Strictly excludes:
    - Carousel items (media_type == 8 or 'carousel_media' in item)
    - Static photo items (media_type == 1 or not a video)
    """
    if not item or not isinstance(item, dict):
        return False

    # Unwrap if wrapped inside 'media' key
    media = item.get("media", item)
    if not isinstance(media, dict):
        return False

    media_type = media.get("media_type")

    # Reject Carousel items
    if media_type == MEDIA_TYPE_CAROUSEL or media_type == 8:
        return False
    if "carousel_media" in media and media["carousel_media"]:
        return False
    if media.get("carousel_media_count", 0) > 0:
        return False

    # Reject Static Photo items
    if media_type == MEDIA_TYPE_PHOTO or media_type == 1:
        return False

    # Accept Video / Reel items
    if media_type == MEDIA_TYPE_VIDEO or media_type == 2:
        return True
    if media.get("is_video") is True:
        return True
    if media.get("product_type") == "clips":
        return True

    return False


def normalize_url(url: str) -> str:
    """
    Cleans and normalizes Instagram URLs into standard https://www.instagram.com/... format.
    Handles user shorthands like '@username', 'username/reels', 'username', ddinstagram, kkinstagram, etc.
    """
    if not url:
        return ""
    url = url.strip()

    # Handle @username shorthand
    if url.startswith("@"):
        username = url.lstrip("@").strip()
        return f"https://www.instagram.com/{username}/"

    # If it's a domain-less string (e.g., 'kanyxxon/reels', 'kanyxxon', 'p/C7xYz')
    has_known_domain = any(
        dom in url.lower()
        for dom in ["instagram.com", "ddinstagram.com", "kkinstagram.com"]
    )

    if not has_known_domain and not url.startswith(("http://", "https://")):
        path = url.strip("/")
        return f"https://www.instagram.com/{path}/"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    if (
        "instagram.com" in netloc
        or "ddinstagram.com" in netloc
        or "kkinstagram.com" in netloc
    ):
        netloc = "www.instagram.com"
    elif not netloc:
        netloc = "www.instagram.com"

    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path

    if not path.endswith("/") and not path.endswith((".jpg", ".png", ".mp4")):
        path += "/"

    clean_url = urlunparse(("https", netloc, path, "", "", ""))
    return clean_url


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Parses and categorizes an Instagram URL into supported target types:
    - profile_reels
    - reel
    - post
    - tv
    - highlight
    - story
    - profile
    - unknown
    """
    if not url:
        return {"type": "unknown", "valid": False, "raw_url": url}

    clean_url = normalize_url(url)

    # 1. Profile Reels Tab: https://www.instagram.com/<username>/reels/
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

    # 2. Single Post / Reel / TV: https://www.instagram.com/reel/<shortcode>/
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

    # 3. Highlights: https://www.instagram.com/stories/highlights/<highlight_id>/
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

    # 4. Stories: https://www.instagram.com/stories/<username>/<story_id>/
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

    # 5. Base User Profile: https://www.instagram.com/<username>/
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
