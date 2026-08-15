"""
core/parser.py - URL decomposition, Regex matching, and MediaID calculation.
"""

import re


def shortcode_to_media_id(shortcode: str) -> int:
    """แปลง Base64 Instagram Shortcode เป็น Integer Media ID สำหรับ REST API"""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        if char in alphabet:
            media_id = media_id * 64 + alphabet.index(char)
    return media_id


def parse_instagram_url(url: str) -> dict | None:
    """วิเคราะห์และแยกประเภท URL ของ Instagram ครบทุกรูปแบบอย่างแม่นยำ"""
    # 1. User Story: /stories/{username}/{story_id}
    story_match = re.search(r"instagram\.com/stories/([A-Za-z0-9_\-\.]+)/([0-9]+)", url)
    if story_match:
        username = story_match.group(1)
        story_id = story_match.group(2)
        return {
            "type": "story",
            "username": username,
            "story_id": story_id,
            "media_id": int(story_id),
            "identifier": f"{username}_{story_id}",
            "clean_url": f"https://www.instagram.com/stories/{username}/{story_id}/",
        }

    # 2. Highlight Story: /stories/highlights/{highlight_id}
    hl_match = re.search(r"instagram\.com/stories/highlights/([0-9]+)", url)
    if hl_match:
        hl_id = hl_match.group(1)
        return {
            "type": "highlight",
            "username": "highlight",
            "story_id": hl_id,
            "media_id": int(hl_id),
            "identifier": f"highlight_{hl_id}",
            "clean_url": f"https://www.instagram.com/stories/highlights/{hl_id}/",
        }

    # 3. Reel / Reels / TV: /(reel|reels|tv)/{shortcode}
    reel_match = re.search(r"instagram\.com/(?:reel|reels|tv)/([A-Za-z0-9_\-\.]+)", url)
    if reel_match:
        shortcode = reel_match.group(1).rstrip("/")
        return {
            "type": "video",
            "username": "Instagram",
            "shortcode": shortcode,
            "media_id": shortcode_to_media_id(shortcode),
            "identifier": shortcode,
            "clean_url": f"https://www.instagram.com/reel/{shortcode}/",
        }

    # 4. Standard Post: /p/{shortcode}
    post_match = re.search(r"instagram\.com/p/([A-Za-z0-9_\-\.]+)", url)
    if post_match:
        shortcode = post_match.group(1).rstrip("/")
        return {
            "type": "post",
            "username": "Instagram",
            "shortcode": shortcode,
            "media_id": shortcode_to_media_id(shortcode),
            "identifier": shortcode,
            "clean_url": f"https://www.instagram.com/p/{shortcode}/",
        }

    return None