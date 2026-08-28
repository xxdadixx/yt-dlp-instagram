"""
core/parser.py - Robust Instagram URL decomposition and normalization.
"""

import re
import urllib.parse
from config.constants import (
    IG_HIGHLIGHT_PATTERN,
    IG_POST_PATTERN,
    IG_PROFILE_PATTERN,
    IG_REELS_TAB_PATTERN,
    IG_STORIES_PATTERN,
)


def normalize_url(url: str) -> str:
    """Strips tracking query parameters while preserving critical path structures."""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    clean_path = parsed.path.rstrip("/") + "/"
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, clean_path, "", "", "")
    )


def parse_instagram_input(raw_text: str) -> list[dict]:
    """
    Parses multiline input strings into clean target descriptors.
    """
    results = []
    lines = raw_text.splitlines()

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        url = normalize_url(cleaned)

        # 1. Reels Tab (/username/reels/)
        reels_match = re.search(IG_REELS_TAB_PATTERN, url, re.IGNORECASE)
        if reels_match:
            username = reels_match.group(1)
            results.append(
                {
                    "type": "profile_reels",
                    "username": username,
                    "url": url,
                }
            )
            continue

        # 2. Stories (/stories/username/123456/)
        story_match = re.search(IG_STORIES_PATTERN, url, re.IGNORECASE)
        if story_match:
            results.append(
                {
                    "type": "stories",
                    "username": story_match.group(1),
                    "url": url,
                }
            )
            continue

        # 3. Highlights (/stories/highlights/123456/)
        highlight_match = re.search(IG_HIGHLIGHT_PATTERN, url, re.IGNORECASE)
        if highlight_match:
            results.append(
                {
                    "type": "highlights",
                    "username": "",
                    "url": url,
                }
            )
            continue

        # 4. Single Post / Reel (/p/CODE/, /reel/CODE/, /tv/CODE/)
        post_match = re.search(IG_POST_PATTERN, url, re.IGNORECASE)
        if post_match:
            results.append(
                {
                    "type": "single_post",
                    "shortcode": post_match.group(1),
                    "url": url,
                }
            )
            continue

        # 5. Profile Feed (/username/)
        profile_match = re.search(IG_PROFILE_PATTERN, url, re.IGNORECASE)
        if profile_match:
            username = profile_match.group(1)
            results.append(
                {
                    "type": "profile_posts",
                    "username": username,
                    "url": url,
                }
            )
            continue

    return results
