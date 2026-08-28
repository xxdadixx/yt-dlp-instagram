"""
core/parser.py - URL decomposition and normalization
"""

import re
from urllib.parse import urlparse, urlunparse
from config.constants import (
    RE_PROFILE_REELS,
    RE_REEL,
    RE_POST,
    RE_STORIES,
    RE_HIGHLIGHTS,
    RE_PROFILE,
)

RESERVED_PATHS = {
    "explore",
    "reels",
    "stories",
    "direct",
    "accounts",
    "legal",
    "about",
    "developer",
    "api",
    "graphql",
    "p",
    "reel",
    "tv",
}


def normalize_url(url: str) -> str:
    """Cleans tracking parameters and normalizes URL to standard HTTPS format."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if "instagram.com" not in netloc:
        return url

    netloc = "www.instagram.com"
    path = parsed.path.rstrip("/") + "/"
    if path == "/":
        path = ""

    return urlunparse(("https", netloc, path, "", "", ""))


def parse_instagram_url(url: str) -> dict | None:
    """
    Parses and categorizes an Instagram URL.
    Returns metadata dictionary or None if unparseable.
    """
    if not url:
        return None

    clean = normalize_url(url)

    # 1. Profile Reels Tab (e.g. /<username>/reels/)
    m = RE_PROFILE_REELS.match(clean)
    if m:
        username = m.group(1).lower()
        if username not in RESERVED_PATHS:
            return {
                "type": "profile_reels",
                "target": username,
                "extra": None,
                "clean_url": f"https://www.instagram.com/{username}/reels/",
                "original_url": url,
            }

    # 2. Single Reel (/reel/<code>/)
    m = RE_REEL.match(clean)
    if m:
        code = m.group(1)
        return {
            "type": "reel",
            "target": code,
            "extra": None,
            "clean_url": f"https://www.instagram.com/reel/{code}/",
            "original_url": url,
        }

    # 3. Single Post (/p/<code>/)
    m = RE_POST.match(clean)
    if m:
        code = m.group(1)
        return {
            "type": "post",
            "target": code,
            "extra": None,
            "clean_url": f"https://www.instagram.com/p/{code}/",
            "original_url": url,
        }

    # 4. Stories Highlights (/stories/highlights/<id>/)
    m = RE_HIGHLIGHTS.match(clean)
    if m:
        highlight_id = m.group(1)
        return {
            "type": "highlight",
            "target": highlight_id,
            "extra": None,
            "clean_url": f"https://www.instagram.com/stories/highlights/{highlight_id}/",
            "original_url": url,
        }

    # 5. User Stories (/stories/<username>/[<story_id>]/)
    m = RE_STORIES.match(clean)
    if m:
        username = m.group(1).lower()
        story_id = m.group(2)
        if username != "highlights":
            return {
                "type": "story",
                "target": username,
                "extra": story_id,
                "clean_url": f"https://www.instagram.com/stories/{username}/"
                + (f"{story_id}/" if story_id else ""),
                "original_url": url,
            }

    # 6. Base Profile (/<username>/)
    m = RE_PROFILE.match(clean)
    if m:
        username = m.group(1).lower()
        if username not in RESERVED_PATHS:
            return {
                "type": "profile",
                "target": username,
                "extra": None,
                "clean_url": f"https://www.instagram.com/{username}/",
                "original_url": url,
            }

    return None
