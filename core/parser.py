"""
core/parser.py - Robust Instagram URL parsing, normalization, and token extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    """Normalizes polymorphic Instagram payloads (GraphQL ASTs & Web REST items)

    into a unified type-safe data model.
    """

    @classmethod
    def parse_graphql_node(cls, node: dict[str, Any]) -> NormalizedMedia | None:
        if not isinstance(node, dict):
            return None

        # Resolve polymorphic ID / Shortcode
        media_id = str(node.get("id") or node.get("pk") or "")
        shortcode = str(node.get("code") or node.get("shortcode") or "")
        if not media_id and not shortcode:
            return None

        # Resolve Owner context
        owner_data = node.get("owner") or node.get("user")
        owner_dict = owner_data if isinstance(owner_data, dict) else {}
        owner_username = str(owner_dict.get("username") or "")
        owner_id = str(owner_dict.get("id") or owner_dict.get("pk") or "")

        # Resolve Captions
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

        taken_at = int(node.get("taken_at_timestamp") or node.get("taken_at") or 0)
        display_url = str(node.get("display_url") or node.get("display_src") or "")

        # Determine Media Type
        typename = str(node.get("__typename") or "")
        is_video_flag = bool(node.get("is_video") or node.get("media_type") == 2)

        assets: list[MediaAsset] = []
        carousel_children: list[NormalizedMedia] = []

        # Case 1: Carousel / Sidecar Container
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

        # Case 2: Video Asset (Reels, Clip, Single Video Post)
        elif is_video_flag or typename == "GraphVideo":
            media_type = "video"
            video_url = str(node.get("video_url") or "")

            video_versions = node.get("video_versions")
            if isinstance(video_versions, list) and len(video_versions) > 0:
                for v in video_versions:
                    if isinstance(v, dict) and v.get("url"):
                        assets.append(
                            MediaAsset(
                                url=str(v["url"]),
                                width=int(v.get("width") or 0),
                                height=int(v.get("height") or 0),
                                is_video=True,
                                mime_type="video/mp4",
                            )
                        )
            elif video_url:
                dim = node.get("dimensions")
                dim_dict = dim if isinstance(dim, dict) else {}
                assets.append(
                    MediaAsset(
                        url=video_url,
                        width=int(dim_dict.get("width") or 0),
                        height=int(dim_dict.get("height") or 0),
                        is_video=True,
                        mime_type="video/mp4",
                    )
                )

            dash_xml = node.get("video_dash_manifest") or node.get("dash_manifest")
            if isinstance(dash_xml, str) and assets:
                assets[0].dash_manifest = dash_xml

        # Case 3: Single Image Asset
        else:
            media_type = "image"
            display_resources = node.get("display_resources")
            if isinstance(display_resources, list) and len(display_resources) > 0:
                for res in display_resources:
                    if isinstance(res, dict) and res.get("src"):
                        assets.append(
                            MediaAsset(
                                url=str(res["src"]),
                                width=int(res.get("config_width") or 0),
                                height=int(res.get("config_height") or 0),
                                is_video=False,
                                mime_type="image/jpeg",
                            )
                        )
            elif display_url:
                dim = node.get("dimensions")
                dim_dict = dim if isinstance(dim, dict) else {}
                assets.append(
                    MediaAsset(
                        url=display_url,
                        width=int(dim_dict.get("width") or 0),
                        height=int(dim_dict.get("height") or 0),
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


ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


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


def parse_instagram_url(url: str) -> Dict[str, Any]:
    """
    Parses and normalizes Instagram URLs into routing targets matching InspectWorker's schema.
    Supports raw URLs, handle strings (@username), and query stripping.
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

    # Handle raw usernames prefixed with @
    if cleaned_url.startswith("@"):
        clean_user = cleaned_url.lstrip("@").strip()
        return {
            "valid": bool(clean_user),
            "type": "profile",
            "username": clean_user,
            "shortcode": None,
            "identifier": f"{clean_user}_all_posts",
        }

    # Handle domainless / shorthand input paths
    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://www.instagram.com/" + cleaned_url.lstrip("/")

    parsed = urlparse(cleaned_url)
    path = parsed.path.strip("/")
    segments = [seg for seg in path.split("/") if seg]

    if not segments:
        return {
            "valid": False,
            "type": "unknown",
            "username": None,
            "shortcode": None,
            "identifier": None,
        }

    first = segments[0].lower()

    # Direct posts / reels / TV: /p/{code}, /reel/{code}, /reels/{code}, /tv/{code}
    if first in {"p", "post", "reel", "reels", "tv"} and len(segments) >= 2:
        code = segments[1]
        media_type = "reel" if first in {"reel", "reels"} else "post"
        if "img_index=" in parsed.query:
            media_type = "carousel"
        return {
            "valid": True,
            "type": media_type,
            "username": None,
            "shortcode": code,
            "identifier": code,
        }

    # Stories: /stories/{username}/{story_id?}
    if first == "stories" and len(segments) >= 2:
        user = segments[1]
        story_id = segments[2] if len(segments) >= 3 else None
        return {
            "valid": True,
            "type": "story" if story_id else "story_user",
            "username": user,
            "shortcode": story_id,
            "identifier": story_id or f"{user}_all_stories",
        }

    # User Profile or Reels Tab: /{username}/ or /{username}/reels/
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


def shortcode_to_id(shortcode: str) -> Optional[int]:
    """Converts an Instagram Base64 shortcode to a numeric media ID."""
    if not shortcode or not isinstance(shortcode, str):
        return None
    media_id = 0
    for char in shortcode:
        if char not in ALPHABET:
            return None
        media_id = (media_id * 64) + ALPHABET.index(char)
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
