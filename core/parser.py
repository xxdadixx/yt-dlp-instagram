"""
core/parser.py - Robust Instagram URL parsing, normalization, and token extraction.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Support alphanumeric, underscore, and dot characters in usernames
PROFILE_REGEX = re.compile(
    r"^(?:https?://)?(?:www\.)?instagram\.com/(?!(?:p|reel|reels|stories|explore|direct|accounts|tv)/)([a-zA-Z0-9_.]+)/?",
    re.IGNORECASE,
)


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


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Parses and categorizes an Instagram URL into structured metadata.
    Distinguishes profile reels tabs (/username/reels/) from general profiles (/username/).
    """
    if not url or not isinstance(url, str):
        return {"type": "unknown", "url": ""}

    clean_url = normalize_url(url)

    # 1. Single Reels
    reel_m = re.search(
        r"instagram\.com/(?:reel|reels)/([A-Za-z0-9_-]+)", clean_url, re.IGNORECASE
    )
    if reel_m:
        shortcode = reel_m.group(1)
        return {
            "type": "reel",
            "shortcode": shortcode,
            "url": f"https://www.instagram.com/reel/{shortcode}/",
            "target_id": shortcode,
        }

    # 2. Highlights
    hl_m = re.search(
        r"instagram\.com/stories/highlights/([0-9A-Za-z_-]+)", clean_url, re.IGNORECASE
    )
    if hl_m:
        highlight_id = hl_m.group(1)
        return {
            "type": "highlight",
            "target_id": highlight_id,
            "url": f"https://www.instagram.com/stories/highlights/{highlight_id}/",
        }

    # 3. Stories
    story_m = re.search(
        r"instagram\.com/stories/([^/?#]+)(?:/([0-9]+))?", clean_url, re.IGNORECASE
    )
    if story_m:
        username = story_m.group(1)
        story_id = story_m.group(2)
        return {
            "type": "story",
            "username": username,
            "story_id": story_id,
            "url": clean_url,
        }

    # 4. Posts / Carousels
    post_m = re.search(r"instagram\.com/p/([A-Za-z0-9_-]+)", clean_url, re.IGNORECASE)
    if post_m:
        shortcode = post_m.group(1)
        return {
            "type": "post",
            "shortcode": shortcode,
            "url": f"https://www.instagram.com/p/{shortcode}/",
            "target_id": shortcode,
        }

    # 5. IGTV
    tv_m = re.search(r"instagram\.com/tv/([A-Za-z0-9_-]+)", clean_url, re.IGNORECASE)
    if tv_m:
        shortcode = tv_m.group(1)
        return {
            "type": "tv",
            "shortcode": shortcode,
            "url": f"https://www.instagram.com/tv/{shortcode}/",
            "target_id": shortcode,
        }

    # 6. Audio
    audio_m = re.search(
        r"instagram\.com/reels/audio/([0-9]+)", clean_url, re.IGNORECASE
    )
    if audio_m:
        audio_id = audio_m.group(1)
        return {
            "type": "audio",
            "target_id": audio_id,
            "url": f"https://www.instagram.com/reels/audio/{audio_id}/",
        }

    # 7. Profile Reels Tab (e.g., https://www.instagram.com/username/reels)
    prof_reels_m = re.search(
        r"instagram\.com/([A-Za-z0-9_.]+)/(?:reels|reel)/?", clean_url, re.IGNORECASE
    )
    if prof_reels_m:
        username = prof_reels_m.group(1)
        if username.lower() not in (
            "p",
            "reel",
            "reels",
            "stories",
            "tv",
            "explore",
            "direct",
            "audio",
        ):
            return {
                "type": "profile_reels",
                "username": username,
                "url": f"https://www.instagram.com/{username}/reels/",
                "target_id": username,
            }

    # 8. General Profile (e.g., https://www.instagram.com/username)
    prof_m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?", clean_url, re.IGNORECASE)
    if prof_m:
        username = prof_m.group(1)
        if username.lower() not in (
            "p",
            "reel",
            "reels",
            "stories",
            "tv",
            "explore",
            "direct",
            "audio",
        ):
            return {
                "type": "profile",
                "username": username,
                "url": f"https://www.instagram.com/{username}/",
                "target_id": username,
            }

    return {"type": "unknown", "url": clean_url}


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
