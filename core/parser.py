"""
core/parser.py - Robust Instagram URL parsing, normalization, and token extraction.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Support alphanumeric, underscore, and dot characters in usernames
PROFILE_REGEX = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/(?!(?:p|reel|reels|stories|explore|direct|accounts|tv)/)([a-zA-Z0-9_.]+)/?",
    re.IGNORECASE,
)

RESERVED_ROOT_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "direct",
    "accounts",
    "api",
    "graphql",
    "developer",
}


def sanitize_instagram_url(url: str) -> str:
    """Strip tracking and pagination query parameters from the Instagram URL."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    # Retain scheme and netloc if present
    path = parsed.path.rstrip("/")
    return (
        f"https://www.instagram.com{path}"
        if not parsed.netloc
        else f"{parsed.scheme}://{parsed.netloc}{path}"
    )


def extract_username_from_url(url: str) -> str | None:
    """Extract clean username from a profile URL."""
    match = PROFILE_REGEX.search(url.strip())
    if match:
        username = match.group(1).strip()
        # Ensure it is not a system route
        if username.lower() not in {
            "p",
            "reel",
            "reels",
            "stories",
            "explore",
            "direct",
            "accounts",
            "tv",
        }:
            return username
    return None


def extract_instagram_urls(text: str) -> List[str]:
    """Extracts all valid Instagram URLs from a block of raw text."""
    if not text or not isinstance(text, str):
        return []

    pattern = re.compile(
        r'https?://(?:www\.)?instagram\.com/[^\s"\'<>]+', re.IGNORECASE
    )
    matches = pattern.findall(text)

    unique_urls: List[str] = []
    for match in matches:
        clean = normalize_url(match)
        if clean and clean not in unique_urls:
            unique_urls.append(clean)
    return unique_urls


def normalize_url(url: str) -> str:
    """Sanitizes Instagram URLs by removing tracking parameters and carousel slide indices."""
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip()
    cleaned = re.sub(r"([?&])img_index=\d+(&?)", r"\1\2", cleaned)
    cleaned = re.sub(r"[?&](?:igsh|utm_[^&=]+|hl|locale)=[^&#]*", "", cleaned).rstrip(
        "?&#"
    )
    cleaned = cleaned.split("?")[0].rstrip("/")
    return cleaned


def parse_instagram_url(url: str) -> Dict[str, Optional[str]]:
    """
    Parses and normalizes Instagram URLs into routing targets matching InspectWorker's schema.
    """
    cleaned_url = (url or "").strip()
    parsed = urlparse(cleaned_url)
    path = parsed.path.strip("/")
    segments = [seg for seg in path.split("/") if seg]

    if not segments:
        return {"type": "unknown", "username": None, "shortcode": None}

    first = segments[0].lower()

    # Single media: /p/{shortcode}, /reel/{shortcode}, /reels/{shortcode}, /tv/{shortcode}
    if first in {"p", "reel", "reels", "tv"} and len(segments) >= 2:
        return {
            "type": "reel" if first in {"reel", "reels"} else "post",
            "username": None,
            "shortcode": segments[1],
        }

    # Stories: /stories/{username}/{story_id?}
    if first == "stories" and len(segments) >= 2:
        return {
            "type": "story",
            "username": segments[1],
            "shortcode": segments[2] if len(segments) >= 3 else None,
        }

    # User profile / Reels tab: /{username}/reels/ or /{username}/
    if first not in RESERVED_ROOT_PATHS:
        username = segments[0]
        if len(segments) >= 2 and segments[1].lower() in {"reels", "reel"}:
            return {
                "type": "profile_reels",
                "username": username,
                "shortcode": None,
            }
        return {
            "type": "profile",
            "username": username,
            "shortcode": None,
        }

    return {"type": "unknown", "username": None, "shortcode": None}


def shortcode_to_id(shortcode: str) -> Optional[int]:
    """Converts an Instagram Base64 shortcode to a numeric media ID."""
    if not shortcode:
        return None
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        if char not in alphabet:
            return None
        media_id = (media_id * 64) + alphabet.index(char)
    return media_id


def is_standalone_video(media_dict: Dict[str, Any]) -> bool:
    """Identifies if an Instagram API payload dictionary corresponds to a video stream."""
    if not isinstance(media_dict, dict):
        return False
    return bool(
        media_dict.get("is_video")
        or media_dict.get("media_type") == 2
        or media_dict.get("video_versions")
        or media_dict.get("product_type") == "clips"
        or media_dict.get("__typename") == "GraphVideo"
    )
