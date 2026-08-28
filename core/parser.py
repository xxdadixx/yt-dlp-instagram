"""
core/parser.py - URL parsing, data normalization, media ID encoding, and standalone video filtering.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from config.constants import (
    AUDIO_REGEX,
    HIGHLIGHTS_REGEX,
    INSTAGRAM_DOMAINS,
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
    if isinstance(shortcode, int):
        return shortcode
    shortcode = str(shortcode).strip()
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
    if bool(media.get("video_versions")):
        return True

    return False


def normalize_url(url: str) -> str:
    """
    Cleans and normalizes Instagram URLs into standard https://www.instagram.com/... format.
    Validates against recognized Instagram domains, handles, and path patterns.
    Rejects non-Instagram or arbitrary random strings.
    """
    if not url:
        return ""
    url = url.strip()

    # Handle @username shorthand
    if url.startswith("@"):
        username = url.lstrip("@").strip()
        if USERNAME_PATTERN.match(username):
            return f"https://www.instagram.com/{username}/"
        return ""

    has_known_domain = any(dom in url.lower() for dom in INSTAGRAM_DOMAINS)
    if not has_known_domain:
        if not url.startswith(("http://", "https://")):
            if PATH_PATTERN.match(url):
                return f"https://www.instagram.com/{url.strip('/')}/"
            if (
                USERNAME_PATTERN.match(url)
                and url.lower() not in RESERVED_USERNAMES
                and " " not in url
                and "." not in url
            ):
                return f"https://www.instagram.com/{url}/"
        return ""

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    if any(dom in netloc for dom in INSTAGRAM_DOMAINS):
        netloc = "www.instagram.com"
    else:
        return ""

    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path

    if not path.endswith("/") and not path.endswith((".jpg", ".png", ".mp4")):
        path += "/"

    clean_url = urlunparse(("https", netloc, path, "", "", ""))
    return clean_url


def extract_instagram_urls(text: str) -> list[str]:
    """
    Extracts all valid Instagram URLs from arbitrary text or clipboard input,
    filtering out non-Instagram text, other websites, or blank lines.
    """
    if not text:
        return []

    patterns = [
        r"https?:\/\/(?:[a-zA-Z0-9_\-]+\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am)\/[^\s,]+",
        r"@[a-zA-Z0-9_\.]{1,30}",
    ]
    candidates: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            candidates.append(m.group(0).strip())

    for line in text.splitlines():
        line = line.strip()
        if line and line not in candidates:
            candidates.append(line)

    valid_targets: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        norm = normalize_url(cand)
        if norm and norm not in seen:
            info = parse_instagram_url(norm)
            if info.get("valid") and info.get("type") != "unknown":
                clean_u = info.get("clean_url") or norm
                if clean_u not in seen:
                    seen.add(clean_u)
                    valid_targets.append(clean_u)

    return valid_targets


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Parses and categorizes an Instagram URL into supported target types:
    - profile_reels, reel, post, tv, highlight, story, audio, profile, unknown
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

    # 2. Audio Page: https://www.instagram.com/reels/audio/<audio_id>/ or /audio/<audio_id>/
    audio_match = AUDIO_REGEX.match(clean_url)
    if audio_match:
        audio_id = audio_match.group(1)
        return {
            "type": "audio",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": audio_id,
            "clean_url": clean_url,
            "raw_url": url,
        }

    # 3. Single Post / Reel / TV / Share: https://www.instagram.com/reel/<shortcode>/
    post_match = POST_REEL_REGEX.match(clean_url)
    if post_match:
        shortcode = post_match.group(1)
        url_lower = clean_url.lower()
        if "/reel/" in url_lower or "/reels/" in url_lower:
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

    # 4. Highlights: https://www.instagram.com/stories/highlights/<id>/ or /s/<shortcode>
    highlight_match = HIGHLIGHTS_REGEX.match(clean_url)
    if highlight_match:
        highlight_id = highlight_match.group(1) or highlight_match.group(2)
        return {
            "type": "highlight",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": highlight_id,
            "clean_url": clean_url,
            "raw_url": url,
        }

    # 5. Stories: https://www.instagram.com/stories/<username>/<story_id>/
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

    # 6. Base User Profile: https://www.instagram.com/<username>/
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
