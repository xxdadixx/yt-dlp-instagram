"""
core/parser.py - Robust Instagram URL parsing, normalization, and token extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

# Base64 shortcode alphabet used by Instagram
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

RESERVED_ROOT_PATHS = {
    "p",
    "post",
    "reel",
    "reels",
    "tv",
    "stories",
    "highlights",
    "explore",
    "direct",
    "accounts",
    "api",
    "graphql",
    "developer",
    "about",
    "legal",
    "share",
}

PROFILE_REGEX = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am|ig\.me)/(?!(?:p|post|reel|reels|stories|highlights|explore|direct|accounts|tv|share|developer|api)/)([a-zA-Z0-9_.]+)/?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class MediaAsset:
    url: str
    width: int
    height: int
    is_video: bool = False
    mime_type: str = "image/jpeg"
    dash_manifest: str | None = None


@dataclass(slots=True)
class NormalizedMedia:
    media_id: str
    shortcode: str
    media_type: str  # "image" | "video" | "carousel"
    owner_username: str
    owner_id: str
    caption: str
    taken_at_timestamp: int
    display_url: str
    assets: list[MediaAsset] = field(default_factory=list)
    carousel_children: list[NormalizedMedia] = field(default_factory=list)


class UnifiedInstagramParser:
    """Normalizes polymorphic Instagram payloads into a unified type-safe data model."""

    @classmethod
    def parse_graphql_node(cls, node: dict[str, Any]) -> NormalizedMedia | None:
        if not isinstance(node, dict):
            return None

        # Resolve primary identifiers with fallbacks
        media_id = str(node.get("id") or node.get("pk") or "").strip()
        shortcode = str(node.get("code") or node.get("shortcode") or "").strip()

        if not media_id and shortcode:
            decoded_id = shortcode_to_id(shortcode)
            if decoded_id is not None:
                media_id = str(decoded_id)

        if not media_id and not shortcode:
            return None

        owner_data = node.get("owner") or node.get("user")
        owner_dict = owner_data if isinstance(owner_data, dict) else {}
        owner_username = str(owner_dict.get("username") or "").strip()
        owner_id = str(owner_dict.get("id") or owner_dict.get("pk") or "").strip()

        caption = ""
        caption_data = node.get("edge_media_to_caption")
        if isinstance(caption_data, dict):
            edges = caption_data.get("edges")
            if isinstance(edges, list) and len(edges) > 0:
                first_edge = edges[0]
                if isinstance(first_edge, dict):
                    node_edge = first_edge.get("node")
                    if isinstance(node_edge, dict):
                        caption = str(node_edge.get("text") or "")
        elif "caption" in node:
            caption_field = node.get("caption")
            if isinstance(caption_field, dict):
                caption = str(caption_field.get("text") or "")
            elif isinstance(caption_field, str):
                caption = caption_field

        try:
            taken_at = int(node.get("taken_at_timestamp") or node.get("taken_at") or 0)
        except (ValueError, TypeError):
            taken_at = 0

        display_url = str(node.get("display_url") or node.get("display_src") or "")

        typename = str(node.get("__typename") or "")
        is_video_flag = bool(
            node.get("is_video")
            or node.get("media_type") == 2
            or node.get("product_type") == "clips"
        )

        assets: list[MediaAsset] = []
        carousel_children: list[NormalizedMedia] = []

        sidecar_data = node.get("edge_sidecar_to_children") or node.get(
            "carousel_media"
        )
        if typename == "GraphSidecar" or isinstance(sidecar_data, (dict, list)):
            child_nodes: list[dict[str, Any]] = []
            if isinstance(sidecar_data, dict):
                child_edges = sidecar_data.get("edges")
                if isinstance(child_edges, list):
                    for edge in child_edges:
                        if isinstance(edge, dict) and isinstance(
                            edge.get("node"), dict
                        ):
                            child_nodes.append(edge["node"])
            elif isinstance(sidecar_data, list):
                child_nodes = [item for item in sidecar_data if isinstance(item, dict)]

            for child in child_nodes:
                parsed_child = cls.parse_graphql_node(child)
                if parsed_child is not None:
                    carousel_children.append(parsed_child)

            media_type = "carousel"
            if not display_url and carousel_children:
                display_url = carousel_children[0].display_url

        elif is_video_flag or typename == "GraphVideo":
            media_type = "video"
            video_url = str(node.get("video_url") or "")

            video_versions = node.get("video_versions")
            if isinstance(video_versions, list) and len(video_versions) > 0:
                for v in video_versions:
                    if isinstance(v, dict) and v.get("url"):
                        try:
                            w = int(v.get("width") or 0)
                            h = int(v.get("height") or 0)
                        except (ValueError, TypeError):
                            w, h = 0, 0
                        assets.append(
                            MediaAsset(
                                url=str(v["url"]),
                                width=w,
                                height=h,
                                is_video=True,
                                mime_type="video/mp4",
                            )
                        )
            elif video_url:
                dim = node.get("dimensions")
                dim_dict = dim if isinstance(dim, dict) else {}
                try:
                    w = int(dim_dict.get("width") or 0)
                    h = int(dim_dict.get("height") or 0)
                except (ValueError, TypeError):
                    w, h = 0, 0
                assets.append(
                    MediaAsset(
                        url=video_url,
                        width=w,
                        height=h,
                        is_video=True,
                        mime_type="video/mp4",
                    )
                )

            dash_xml = node.get("video_dash_manifest") or node.get("dash_manifest")
            if isinstance(dash_xml, str) and assets:
                assets[0].dash_manifest = dash_xml

        else:
            media_type = "image"
            display_resources = node.get("display_resources")
            if isinstance(display_resources, list) and len(display_resources) > 0:
                for res in display_resources:
                    if isinstance(res, dict) and res.get("src"):
                        try:
                            w = int(res.get("config_width") or 0)
                            h = int(res.get("config_height") or 0)
                        except (ValueError, TypeError):
                            w, h = 0, 0
                        assets.append(
                            MediaAsset(
                                url=str(res["src"]),
                                width=w,
                                height=h,
                                is_video=False,
                                mime_type="image/jpeg",
                            )
                        )
            elif display_url:
                dim = node.get("dimensions")
                dim_dict = dim if isinstance(dim, dict) else {}
                try:
                    w = int(dim_dict.get("width") or 0)
                    h = int(dim_dict.get("height") or 0)
                except (ValueError, TypeError):
                    w, h = 0, 0
                assets.append(
                    MediaAsset(
                        url=display_url,
                        width=w,
                        height=h,
                        is_video=False,
                        mime_type="image/jpeg",
                    )
                )

        return NormalizedMedia(
            media_id=media_id,
            shortcode=shortcode,
            media_type=media_type,
            owner_username=owner_username,
            owner_id=owner_id,
            caption=caption,
            taken_at_timestamp=taken_at,
            display_url=display_url,
            assets=assets,
            carousel_children=carousel_children,
        )


def sanitize_instagram_url(url: str) -> str:
    """Strip tracking and pagination query parameters from an Instagram URL."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
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
        if username.lower() not in RESERVED_ROOT_PATHS:
            return username
    return None


def extract_instagram_urls(text: str) -> List[str]:
    """Extracts all valid Instagram URLs from a block of raw text."""
    if not text or not isinstance(text, str):
        return []

    pattern = re.compile(
        r'https?://(?:www\.)?(?:instagram\.com|ddinstagram\.com|kkinstagram\.com|instagr\.am|ig\.me)/[^\s"\'<>]+',
        re.IGNORECASE,
    )
    matches = pattern.findall(text)

    unique_urls: List[str] = []
    for match in matches:
        clean = normalize_url(match)
        if clean and clean not in unique_urls:
            unique_urls.append(clean)
    return unique_urls


def normalize_url(url: str) -> str:
    """Sanitizes Instagram URLs by removing tracking parameters and cleaning formatting."""
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip()
    cleaned = re.sub(r"([?&])img_index=\d+(&?)", r"\1\2", cleaned)
    cleaned = re.sub(r"[?&](?:igsh|utm_[^&=]+|hl|locale)=[^&#]*", "", cleaned).rstrip(
        "?&#"
    )
    return cleaned.split("?")[0].rstrip("/")


def id_to_shortcode(media_id: int | str) -> str:
    """Converts a numeric Instagram media ID to its Base64 shortcode representation."""
    try:
        num = int(media_id)
    except (ValueError, TypeError):
        return ""

    if num == 0:
        return ALPHABET[0]

    shortcode_chars: list[str] = []
    while num > 0:
        num, remainder = divmod(num, 64)
        shortcode_chars.append(ALPHABET[remainder])
    return "".join(reversed(shortcode_chars))


def shortcode_to_id(shortcode: str) -> Optional[int]:
    """Converts an Instagram Base64 shortcode to a numeric 64-bit media ID."""
    if not shortcode or not isinstance(shortcode, str):
        return None
    # Strip URL artifacts, trailing slashes, or query fragments
    clean_code = shortcode.strip().split("?")[0].rstrip("/")
    media_id = 0
    try:
        for char in clean_code:
            if char not in ALPHABET:
                return None
            media_id = (media_id * 64) + ALPHABET.index(char)
        return media_id if media_id > 0 else None
    except Exception:
        return None


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Parses and normalizes Instagram URLs into structured routing targets.
    Correctly resolves direct posts, vanity-prefixed URLs (/username/p/CODE),
    reels, stories, highlights, share redirects, and raw handles.
    """
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        return {
            "valid": False,
            "type": "unknown",
            "username": None,
            "shortcode": None,
            "identifier": None,
        }

    # 1. Handle raw usernames prefixed with @
    if cleaned_url.startswith("@"):
        clean_user = cleaned_url.lstrip("@").strip()
        return {
            "valid": bool(clean_user),
            "type": "profile",
            "username": clean_user,
            "shortcode": None,
            "identifier": f"{clean_user}_all_posts",
        }

    # 2. Add scheme if domainless
    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://www.instagram.com/" + cleaned_url.lstrip("/")

    parsed = urlparse(cleaned_url)
    path = parsed.path.strip("/")
    segments = [seg for seg in path.split("/") if seg]
    query_params = parse_qs(parsed.query)

    if not segments:
        return {
            "valid": False,
            "type": "unknown",
            "username": None,
            "shortcode": None,
            "identifier": None,
        }

    # Strip out 'share' wrappers (e.g., /share/p/{code}, /share/reel/{code})
    if segments[0].lower() == "share":
        segments = segments[1:]
        if not segments:
            return {
                "valid": False,
                "type": "unknown",
                "username": None,
                "shortcode": None,
                "identifier": None,
            }

    first = segments[0].lower()

    # 3. Direct Posts, Reels, and TV Items at root: /p/{code}, /reel/{code}, /tv/{code}
    if first in {"p", "post", "reel", "reels", "tv", "r"}:
        code = segments[1] if len(segments) >= 2 else None
        if not code:
            return {
                "valid": False,
                "type": "unknown",
                "username": None,
                "shortcode": None,
                "identifier": None,
            }

        is_carousel = "img_index" in query_params or "carousel" in cleaned_url
        if is_carousel:
            media_type = "carousel"
        elif first in {"reel", "reels"}:
            media_type = "reel"
        else:
            media_type = "post"

        return {
            "valid": True,
            "type": media_type,
            "username": None,
            "shortcode": code,
            "identifier": code,
        }

    # 4. Stories Routing: /stories/{username}/{story_id?}
    if first == "stories":
        user = segments[1] if len(segments) >= 2 else None
        story_id = segments[2] if len(segments) >= 3 else None
        return {
            "valid": bool(user),
            "type": "story" if story_id else "story_user",
            "username": user,
            "shortcode": story_id,
            "identifier": story_id or (f"{user}_all_stories" if user else None),
        }

    # 5. Highlights: /stories/highlights/{id}/ or /s/{id}
    if first in {"highlights", "s"} or (
        first == "stories"
        and len(segments) >= 2
        and segments[1].lower() == "highlights"
    ):
        code = segments[-1]
        return {
            "valid": True,
            "type": "highlight",
            "username": None,
            "shortcode": code,
            "identifier": code,
        }

    # 6. Vanity-Prefixed Post/Reel URLs: /{username}/p/{code} or /{username}/reel/{code}
    if first not in RESERVED_ROOT_PATHS and len(segments) >= 3:
        sub_action = segments[1].lower()
        if sub_action in {"p", "post", "reel", "reels", "tv", "r"}:
            code = segments[2]
            is_carousel = "img_index" in query_params or "carousel" in cleaned_url
            if is_carousel:
                media_type = "carousel"
            elif sub_action in {"reel", "reels"}:
                media_type = "reel"
            else:
                media_type = "post"

            return {
                "valid": True,
                "type": media_type,
                "username": segments[0].lstrip("@"),
                "shortcode": code,
                "identifier": code,
            }

    # 7. User Profile or Dedicated Reels Tab: /{username}/ or /{username}/reels/
    if first not in RESERVED_ROOT_PATHS:
        username = segments[0].lstrip("@")
        if len(segments) >= 2 and segments[1].lower() in {"reels", "reel"}:
            return {
                "valid": True,
                "type": "profile_reels",
                "username": username,
                "shortcode": None,
                "identifier": f"{username}_all_reels",
            }
        return {
            "valid": True,
            "type": "profile",
            "username": username,
            "shortcode": None,
            "identifier": f"{username}_all_posts",
        }

    return {
        "valid": False,
        "type": "unknown",
        "username": None,
        "shortcode": None,
        "identifier": None,
    }


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
