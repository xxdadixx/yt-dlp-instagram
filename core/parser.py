"""
core/parser.py - URL parsing, data normalization, media ID encoding, and standalone video filtering.
Handles Instagram URLs, tracking parameters, path shorthand, domain aliases, carousel slide indices, and direct CDN streams.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from config.constants import (
    AUDIO_REGEX,
    DIRECT_CDN_REGEX,
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
    if not media_id or media_id < 0:
        return ""
    shortcode_chars = []
    while media_id > 0:
        remainder = media_id % 64
        shortcode_chars.append(IG_ALPHABET[remainder])
        media_id //= 64
    return "".join(reversed(shortcode_chars))


def shortcode_to_id(shortcode: str) -> int:
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
    if not item or not isinstance(item, dict):
        return False

    media = item.get("media", item)
    if not isinstance(media, dict):
        return False

    media_type = media.get("media_type")

    if media_type == MEDIA_TYPE_CAROUSEL or media_type == 8:
        return False
    if "carousel_media" in media and media["carousel_media"]:
        return False
    if media.get("carousel_media_count", 0) > 0:
        return False
    if "edge_sidecar_to_children" in media and media["edge_sidecar_to_children"]:
        return False

    if media_type == MEDIA_TYPE_PHOTO or media_type == 1:
        return False

    if media_type == MEDIA_TYPE_VIDEO or media_type == 2:
        return True
    if media.get("is_video") is True:
        return True
    if media.get("product_type") in ("clips", "feed_video"):
        return True
    if bool(media.get("video_versions")):
        return True

    return False


USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]{1,30}$")
PATH_PATTERN = re.compile(
    r"^[a-zA-Z0-9_.]{1,30}/(?:reels|p|post|reel|tv|r|stories|highlights)/?$",
    re.IGNORECASE,
)
PATH_SHORTHAND_PATTERN = re.compile(
    r"^(?:share/(?:p|post|reel|reels|tv|r|stories|highlights|audio|reels/audio|s)?|[a-zA-Z0-9_.]{1,30}/(?:reels|p|post|reel|tv|r|stories|highlights)|p/|post/|reel/|reels/|tv/|r/|stories/|highlights/|audio/|reels/audio/|s/)[a-zA-Z0-9_\-.]*/?$",
    re.IGNORECASE,
)

PUNCT_CHARS = set(" ()[]{}<>.,!;") | {'"', "'"}


def _strip_punct(s: str) -> str:
    start = 0
    end = len(s)
    while start < end and s[start] in PUNCT_CHARS:
        start += 1
    while end > start and s[end - 1] in PUNCT_CHARS:
        end -= 1
    return s[start:end]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = _strip_punct(url.strip())
    if not url:
        return ""

    if url.startswith("@"):
        username = url.lstrip("@").strip()
        if USERNAME_PATTERN.match(username):
            return f"https://www.instagram.com/{username}/"
        return ""

    # Handle direct CDN media URLs
    if any(cdn in url.lower() for cdn in ("cdninstagram.com", "fbcdn.net")):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    has_known_domain = any(dom in url.lower() for dom in INSTAGRAM_DOMAINS)
    if not has_known_domain:
        if not url.startswith(("http://", "https://")):
            if PATH_PATTERN.match(url) or PATH_SHORTHAND_PATTERN.match(url):
                return normalize_url(f'https://www.instagram.com/{url.strip("/")}/')
            elif (
                USERNAME_PATTERN.match(url)
                and url.lower() not in RESERVED_USERNAMES
                and " " not in url
                and "." not in url
            ):
                return f"https://www.instagram.com/{url}/"
            else:
                return ""
        else:
            return ""

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
    except Exception:
        return ""

    if any(
        dom in netloc
        for dom in (
            "instagram.com",
            "ddinstagram.com",
            "kkinstagram.com",
            "instagr.am",
            "ig.me",
        )
    ):
        netloc = "www.instagram.com"
    elif any(cdn in netloc for cdn in ("cdninstagram.com", "fbcdn.net")):
        return url
    else:
        return ""

    path = parsed.path
    if not path.startswith("/"):
        path = "/" + path

    # Strip subpaths like /embed/, /captioned/, /comments/, /c/123456/
    path = re.sub(
        r"/(?:embed(?:/captioned)?|captioned|comments|c/[a-zA-Z0-9_]+)/?$",
        "/",
        path,
        flags=re.IGNORECASE,
    )

    # Normalize share paths and shorthand paths
    path = re.sub(r"^/share/(?:p|post)/", r"/p/", path, flags=re.IGNORECASE)
    path = re.sub(r"^/share/(?:reel|reels|r)/", r"/reel/", path, flags=re.IGNORECASE)
    path = re.sub(r"^/share/tv/", r"/tv/", path, flags=re.IGNORECASE)
    path = re.sub(
        r"^/share/([a-zA-Z0-9_\-]+)/?$", r"/p/\g<1>/", path, flags=re.IGNORECASE
    )
    path = re.sub(r"^/post/", r"/p/", path, flags=re.IGNORECASE)
    path = re.sub(r"^/r/", r"/reel/", path, flags=re.IGNORECASE)
    path = re.sub(r"^/reel/audio/", r"/reels/audio/", path, flags=re.IGNORECASE)
    path = re.sub(
        r"^/stories/highlights/s/", r"/stories/highlights/", path, flags=re.IGNORECASE
    )
    path = re.sub(r"^/s/s/", r"/s/", path, flags=re.IGNORECASE)

    # Normalize /username/p/CODE/ or /username/reel/CODE/ -> /p/CODE/ or /reel/CODE/
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/p/([a-zA-Z0-9_\-]+)/?",
        r"/p/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/reel/([a-zA-Z0-9_\-]+)/?",
        r"/reel/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/reels/([a-zA-Z0-9_\-]+)/?",
        r"/reel/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/post/([a-zA-Z0-9_\-]+)/?",
        r"/p/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/tv/([a-zA-Z0-9_\-]+)/?",
        r"/tv/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(
        r"^/[a-zA-Z0-9_.]+/r/([a-zA-Z0-9_\-]+)/?",
        r"/reel/\g<1>/",
        path,
        flags=re.IGNORECASE,
    )

    if not path.endswith("/") and not path.endswith((".jpg", ".png", ".mp4", ".webp")):
        path += "/"

    clean_url = urlunparse(("https", netloc, path, "", "", ""))
    return clean_url


clean_instagram_url = normalize_url


def extract_instagram_urls(text: str) -> List[str]:
    if not text:
        return []

    url_regex = re.compile(
        r"https?://(?:[a-zA-Z0-9_\-]+\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am|ig\.me|cdninstagram\.com|fbcdn\.net)/[^\s,)\(\]\[\}\"\'><]+",
        re.IGNORECASE,
    )
    user_regex = re.compile(r"@[a-zA-Z0-9_.]{1,30}")

    candidates = []
    for m in url_regex.finditer(text):
        candidates.append(m.group(0).strip())
    for m in user_regex.finditer(text):
        candidates.append(m.group(0).strip())

    for line in text.splitlines():
        line = line.strip()
        cleaned_line = re.sub(r"^(?:\d+[\.\)]|\-|\*)\s+", "", line).strip()
        if cleaned_line and " " not in cleaned_line and cleaned_line not in candidates:
            candidates.append(cleaned_line)

    valid_targets = []
    seen = set()
    for cand in candidates:
        cand_clean = _strip_punct(cand.strip())
        if not cand_clean:
            continue
        try:
            norm = normalize_url(cand_clean)
            if norm:
                info = parse_instagram_url(cand_clean)
                if info.get("valid") and info.get("type") != "unknown":
                    target_url = (
                        cand_clean
                        if info.get("img_index") is not None
                        else (info.get("clean_url") or norm)
                    )
                    if target_url not in seen:
                        seen.add(target_url)
                        valid_targets.append(target_url)
        except Exception:
            continue

    return valid_targets


def parse_instagram_url(url: str) -> Dict[str, Any]:
    if not url:
        return {
            "type": "unknown",
            "valid": False,
            "raw_url": url,
            "img_index": None,
            "description": "Invalid URL",
        }

    clean_url = normalize_url(url)
    if not clean_url:
        return {
            "type": "unknown",
            "valid": False,
            "raw_url": url,
            "img_index": None,
            "description": "Invalid URL",
        }

    # Direct CDN media stream
    if DIRECT_CDN_REGEX.match(clean_url) or any(
        cdn in clean_url.lower() for cdn in ("cdninstagram.com", "fbcdn.net")
    ):
        return {
            "type": "direct_media",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": None,
            "img_index": None,
            "clean_url": clean_url,
            "raw_url": url,
            "description": "Direct Media Stream",
        }

    img_index = None
    try:
        parsed_raw = urlparse(url)
        qs = parse_qs(parsed_raw.query)
        if "img_index" in qs and qs["img_index"]:
            try:
                img_index = int(qs["img_index"][0])
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

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
                "img_index": None,
                "clean_url": clean_url,
                "raw_url": url,
                "description": f"@{username} Reels Tab",
            }

    audio_match = AUDIO_REGEX.match(clean_url)
    if audio_match:
        audio_id = audio_match.group(1)
        return {
            "type": "audio",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": audio_id,
            "img_index": None,
            "clean_url": clean_url,
            "raw_url": url,
            "description": f"Audio Track (ID: #{audio_id})",
        }

    post_match = POST_REEL_REGEX.match(clean_url)
    if post_match:
        shortcode = next((g for g in post_match.groups() if g), "")
        url_lower = clean_url.lower()
        if "/reel/" in url_lower or "/reels/" in url_lower or "/r/" in url_lower:
            media_type = "reel"
            desc = f"Instagram Reel (#{shortcode})"
        elif "/tv/" in url_lower:
            media_type = "tv"
            desc = f"IGTV Video (#{shortcode})"
        elif img_index is not None:
            media_type = "carousel"
            desc = f"Carousel Post (#{shortcode} • Slide {img_index})"
        else:
            media_type = "post"
            desc = f"Instagram Post (#{shortcode})"

        return {
            "type": media_type,
            "valid": True,
            "username": None,
            "shortcode": shortcode,
            "target_id": None,
            "img_index": img_index,
            "clean_url": clean_url,
            "raw_url": url,
            "description": desc,
        }

    highlight_match = HIGHLIGHTS_REGEX.match(clean_url)
    if highlight_match:
        highlight_id = next((g for g in highlight_match.groups() if g), "")
        return {
            "type": "highlight",
            "valid": True,
            "username": None,
            "shortcode": None,
            "target_id": highlight_id,
            "img_index": None,
            "clean_url": clean_url,
            "raw_url": url,
            "description": f"Story Highlight (ID: #{highlight_id})",
        }

    story_match = STORIES_REGEX.match(clean_url)
    if story_match:
        groups = [g for g in story_match.groups() if g]
        username = groups[0].lower() if groups else ""
        story_id = groups[1] if len(groups) > 1 else None
        if username and username not in RESERVED_USERNAMES:
            return {
                "type": "story",
                "valid": True,
                "username": username,
                "shortcode": None,
                "target_id": story_id,
                "img_index": None,
                "clean_url": clean_url,
                "raw_url": url,
                "description": (
                    f"@{username} Story (ID: #{story_id})"
                    if story_id
                    else f"@{username} Story"
                ),
            }

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
                "img_index": None,
                "clean_url": clean_url,
                "raw_url": url,
                "description": f"@{username} Profile",
            }

    return {
        "type": "unknown",
        "valid": False,
        "username": None,
        "shortcode": None,
        "target_id": None,
        "img_index": None,
        "clean_url": clean_url,
        "raw_url": url,
        "description": "Unknown Target",
    }


parse_url = parse_instagram_url


def normalize_instagram_url(url: str) -> tuple[str, str]:
    """
    Cleans tracking/slide query parameters while preserving story usernames,
    and returns (clean_url, media_type).
    """
    url = url.strip()
    parsed = urlparse(url)

    # Strip unnecessary query parameters (like ?img_index=1, ?igsh=..., etc.)
    # Carousel posts need the base post URL to fetch all slides.
    clean_url = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", "", "")
    )

    # Detect Media Type
    path = parsed.path.strip("/")
    if re.match(r"^stories/highlights/", path) or re.match(r"^s/[A-Za-z0-9_=-]+", path):
        media_type = "HIGHLIGHT"
    elif re.match(r"^stories/[^/]+/[0-9]+", path):
        media_type = "STORY"
    elif re.match(r"^stories/[^/]+", path):
        media_type = "STORIES"  # User story reel / multiple stories
    elif re.match(r"^(?:reel|reels)/", path):
        media_type = "REEL"
    elif re.match(r"^p/[^/]+", path):
        media_type = "POST"  # Could be single image/video or carousel
    else:
        media_type = "PROFILE"

    return clean_url, media_type
