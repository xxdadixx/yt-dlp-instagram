"""
core/inspect_worker.py - Background QThread for inspecting Instagram media payloads.
Features Dedicated User Stories Engine, Profile Feed Scraper, Infinite Reels Pagination,
and Duplicate Detection against existing/completed grid items.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp

from config.constants import DESKTOP_UA, IG_APP_ID, MOBILE_UA
from core.cookie_manager import get_cookie_opener
from core.parser import parse_instagram_url
from utils.logger import SilentLogger


class InspectionWorker(QThread):
    item_inspected = pyqtSignal(dict)
    finished_inspection = pyqtSignal(int)
    progress_status = pyqtSignal(str)

    def __init__(
        self,
        targets: list[dict | str],
        cookie_path: str | None,
        existing_shortcodes: set[str] | None = None,
    ):
        super().__init__()
        self.targets = targets
        self.cookie_path = cookie_path
        self.existing_shortcodes = (
            set(existing_shortcodes) if existing_shortcodes else set()
        )
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def _get_csrf_token(self) -> str:
        if not self.cookie_path or not os.path.exists(self.cookie_path):
            return ""
        try:
            with open(self.cookie_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "csrftoken" in line and not line.startswith("#"):
                        parts = line.strip().split("\t")
                        if len(parts) >= 7:
                            return parts[6]
        except Exception:
            pass
        return ""

    def _build_web_headers(self, referer_url: str) -> dict:
        csrf = self._get_csrf_token()
        headers = {
            "User-Agent": DESKTOP_UA,
            "X-IG-App-ID": IG_APP_ID,
            "X-ASBD-ID": "129477",
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer_url,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if csrf:
            headers["X-CSRFToken"] = csrf
        return headers

    def _get_user_id(
        self, username: str, opener: urllib.request.OpenerDirector
    ) -> int | None:
        # Method 1: Web Profile Info
        try:
            headers = self._build_web_headers(f"https://www.instagram.com/{username}/")
            profile_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            req = urllib.request.Request(profile_url, headers=headers)
            with opener.open(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_id = data.get("data", {}).get("user", {}).get("id") or data.get(
                    "data", {}
                ).get("user", {}).get("pk")
                if user_id:
                    return int(user_id)
        except Exception as e:
            print(f"[DEBUG] Web user lookup error: {e}")

        # Method 2: Mobile Username Info
        try:
            url = f"https://i.instagram.com/api/v1/users/{username}/usernameinfo/"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": MOBILE_UA,
                    "X-IG-App-ID": IG_APP_ID,
                    "Accept": "*/*",
                },
            )
            with opener.open(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_pk = data.get("user", {}).get("pk") or data.get("user", {}).get(
                    "pk_id"
                )
                if user_pk:
                    return int(user_pk)
        except Exception as e:
            print(f"[DEBUG] Mobile user lookup error: {e}")

        return None

    def _fetch_user_stories_web(
        self, username: str, user_id: int, opener: urllib.request.OpenerDirector
    ) -> int:
        found_count = 0
        seen_codes = set()
        headers = self._build_web_headers(
            f"https://www.instagram.com/stories/{username}/"
        )
        self.progress_status.emit(f"Fetching active stories from @{username}...")

        try:
            story_url = (
                f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}"
            )
            req = urllib.request.Request(story_url, headers=headers)
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                reels_data = data.get("reels", {}) or data.get("reels_media", {})

                user_reel = {}
                if isinstance(reels_data, dict):
                    user_reel = reels_data.get(str(user_id), {})
                elif isinstance(reels_data, list) and reels_data:
                    user_reel = reels_data[0]

                items = user_reel.get("items", [])
                for post in items:
                    if self._is_cancelled:
                        break

                    story_pk = str(post.get("pk") or post.get("id", ""))
                    if (
                        not story_pk
                        or story_pk in seen_codes
                        or story_pk in self.existing_shortcodes
                    ):
                        continue
                    seen_codes.add(story_pk)

                    v_list = post.get("video_versions", [])
                    is_story_video = bool(
                        v_list or post.get("is_video") or post.get("media_type") == 2
                    )
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    thumb = cands[0]["url"] if cands else ""

                    single_item = {
                        "url": f"https://www.instagram.com/stories/{username}/{story_pk}/",
                        "shortcode": story_pk,
                        "uploader": username,
                        "thumb_url": thumb,
                        "media_type": "story",
                        "slides_count": 1,
                        "format_options": [],
                        "raw_media_items": [],
                    }

                    if is_story_video:
                        best_v = (
                            max(v_list, key=lambda x: int(x.get("width", 0)))
                            if v_list
                            else {}
                        )
                        vw = best_v.get("width", 1080)
                        vh = best_v.get("height", 1920)
                        single_item["format_options"] = [
                            {
                                "label": f"🎬 Story Video ({vw}x{vh} - Best)",
                                "key": "story_video",
                            },
                            {
                                "label": "🎵 Audio Only (MP3 192kbps)",
                                "key": "audio_mp3",
                            },
                        ]
                        if best_v.get("url"):
                            single_item["raw_media_items"].append(
                                {
                                    "url": best_v["url"],
                                    "ext": "mp4",
                                    "is_video": True,
                                }
                            )
                    else:
                        w = cands[0].get("width", 1080) if cands else 1080
                        h = cands[0].get("height", 1920) if cands else 1920
                        single_item["format_options"] = [
                            {
                                "label": f"🖼️ Story Photo ({w}x{h} - Original)",
                                "key": "story_photo",
                            }
                        ]
                        if cands:
                            best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                            single_item["raw_media_items"].append(
                                {
                                    "url": best_s["url"],
                                    "ext": "jpg",
                                    "is_video": False,
                                }
                            )

                    self.item_inspected.emit(single_item)
                    found_count += 1
                    self.progress_status.emit(
                        f"Found [{found_count}] stories from @{username}"
                    )
                    time.sleep(0.015)

        except Exception as e:
            print(f"[DEBUG] Story fetch error: {e}")

        return found_count

    def _fetch_profile_feed_web(
        self, username: str, opener: urllib.request.OpenerDirector, scope: str = "all"
    ) -> int:
        found_count = 0
        seen_codes = set()
        user_id = None
        headers = self._build_web_headers(f"https://www.instagram.com/{username}/")

        self.progress_status.emit(f"Loading Feed from @{username} (Mode: {scope})...")
        try:
            profile_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            req = urllib.request.Request(profile_url, headers=headers)
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_data = data.get("data", {}).get("user", {})
                if user_data:
                    user_id = user_data.get("id") or user_data.get("pk")
                    timeline_edges = user_data.get(
                        "edge_owner_to_timeline_media", {}
                    ).get("edges", [])
                    felix_edges = user_data.get("edge_felix_video_timeline", {}).get(
                        "edges", []
                    )

                    all_edges = (
                        felix_edges + timeline_edges
                        if scope in ("all", "videos_only")
                        else timeline_edges
                    )

                    for edge in all_edges:
                        if self._is_cancelled:
                            break
                        node = edge.get("node", {})
                        sc = node.get("shortcode") or node.get("id")
                        if not sc or sc in seen_codes or sc in self.existing_shortcodes:
                            continue
                        seen_codes.add(sc)

                        typename = node.get("__typename", "")
                        is_vid = bool(node.get("is_video") or typename == "GraphVideo")
                        thumb = node.get("display_url", "")

                        if (
                            scope == "videos_only"
                            and not is_vid
                            and typename != "GraphSidecar"
                        ):
                            continue
                        if scope == "photos_only" and is_vid:
                            continue

                        if typename == "GraphSidecar":
                            children = node.get("edge_sidecar_to_children", {}).get(
                                "edges", []
                            )
                            raw_media = []
                            for c in children:
                                c_node = c.get("node", {})
                                c_is_vid = c_node.get("is_video", False)
                                c_url = (
                                    c_node.get("video_url")
                                    if c_is_vid
                                    else c_node.get("display_url")
                                )
                                if not c_url:
                                    continue

                                if scope == "photos_only" and c_is_vid:
                                    continue
                                if scope == "videos_only" and not c_is_vid:
                                    continue

                                raw_media.append(
                                    {
                                        "url": c_url,
                                        "ext": "mp4" if c_is_vid else "jpg",
                                        "is_video": c_is_vid,
                                    }
                                )

                            if not raw_media:
                                continue

                            single_item = {
                                "url": f"https://www.instagram.com/p/{sc}/",
                                "shortcode": sc,
                                "uploader": username,
                                "thumb_url": thumb,
                                "media_type": "carousel",
                                "slides_count": len(raw_media),
                                "format_options": [
                                    {
                                        "label": f"⚡ Best Quality - {len(raw_media)} Items",
                                        "key": "best_all",
                                    }
                                ],
                                "raw_media_items": raw_media,
                            }

                        elif is_vid:
                            video_url = node.get("video_url", "")
                            single_item = {
                                "url": f"https://www.instagram.com/reel/{sc}/",
                                "shortcode": sc,
                                "uploader": username,
                                "thumb_url": thumb,
                                "media_type": "video",
                                "slides_count": 1,
                                "format_options": [
                                    {
                                        "label": "🎬 Best Video (Highest Quality)",
                                        "key": "video_best",
                                    },
                                    {
                                        "label": "🎞️ H.264 Compatibility Mode",
                                        "key": "video_h264",
                                    },
                                    {
                                        "label": "🎵 Audio Only (MP3 192kbps)",
                                        "key": "audio_mp3",
                                    },
                                ],
                                "raw_media_items": [],
                            }
                            if video_url:
                                single_item["raw_media_items"].append(
                                    {
                                        "url": video_url,
                                        "ext": "mp4",
                                        "is_video": True,
                                    }
                                )

                        else:
                            single_item = {
                                "url": f"https://www.instagram.com/p/{sc}/",
                                "shortcode": sc,
                                "uploader": username,
                                "thumb_url": thumb,
                                "media_type": "photo",
                                "slides_count": 1,
                                "format_options": [
                                    {
                                        "label": "🖼️ Original Resolution",
                                        "key": "best_single",
                                    },
                                    {
                                        "label": "🖼️ Compressed Web Size",
                                        "key": "720p_single",
                                    },
                                ],
                                "raw_media_items": [
                                    {"url": thumb, "ext": "jpg", "is_video": False}
                                ],
                            }

                        self.item_inspected.emit(single_item)
                        found_count += 1
                        self.progress_status.emit(
                            f"Found [{found_count}] items ({scope}) from @{username}"
                        )
                        time.sleep(0.015)
        except Exception as e:
            print(f"[DEBUG] Profile Feed initial fetch error: {e}")

        if user_id and not self._is_cancelled:
            max_id = None
            page_num = 2

            while not self._is_cancelled:
                self.progress_status.emit(
                    f"Fetching Feed page {page_num} ({scope}) from @{username}..."
                )
                feed_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/"
                if max_id:
                    feed_url += f"?max_id={max_id}"

                req = urllib.request.Request(feed_url, headers=headers)

                try:
                    with opener.open(req, timeout=12) as resp:
                        f_data = json.loads(resp.read().decode("utf-8"))
                except Exception as e:
                    print(f"[DEBUG] Feed Pagination Error on page {page_num}: {e}")
                    break

                raw_items = f_data.get("items", [])
                if not raw_items:
                    break

                batch_added = 0
                for post in raw_items:
                    if self._is_cancelled:
                        break
                    sc = post.get("code")
                    if not sc or sc in seen_codes or sc in self.existing_shortcodes:
                        continue
                    seen_codes.add(sc)

                    m_type_num = post.get("media_type", 1)
                    is_post_vid = bool(m_type_num == 2 or post.get("is_video", False))
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    thumb = cands[0]["url"] if cands else ""

                    if scope == "videos_only" and not is_post_vid and m_type_num != 8:
                        continue
                    if scope == "photos_only" and is_post_vid:
                        continue

                    if m_type_num == 8 and "carousel_media" in post:
                        slides = post.get("carousel_media", [])
                        raw_media = []
                        for s in slides:
                            v_list = s.get("video_versions", [])
                            is_s_vid = bool(
                                v_list or s.get("is_video") or s.get("media_type") == 2
                            )
                            if scope == "photos_only" and is_s_vid:
                                continue
                            if scope == "videos_only" and not is_s_vid:
                                continue

                            if is_s_vid and v_list:
                                best_v = max(
                                    v_list, key=lambda x: int(x.get("width", 0))
                                )
                                raw_media.append(
                                    {
                                        "url": best_v["url"],
                                        "ext": "mp4",
                                        "is_video": True,
                                    }
                                )
                            else:
                                s_cands = s.get("image_versions2", {}).get(
                                    "candidates", []
                                )
                                if s_cands:
                                    best_s = max(
                                        s_cands, key=lambda x: int(x.get("width", 0))
                                    )
                                    raw_media.append(
                                        {
                                            "url": best_s["url"],
                                            "ext": "jpg",
                                            "is_video": False,
                                        }
                                    )

                        if not raw_media:
                            continue

                        single_item = {
                            "url": f"https://www.instagram.com/p/{sc}/",
                            "shortcode": sc,
                            "uploader": username,
                            "thumb_url": thumb,
                            "media_type": "carousel",
                            "slides_count": len(raw_media),
                            "format_options": [
                                {
                                    "label": f"⚡ Best Quality - {len(raw_media)} Items",
                                    "key": "best_all",
                                }
                            ],
                            "raw_media_items": raw_media,
                        }

                    elif is_post_vid:
                        v_list = post.get("video_versions", [])
                        single_item = {
                            "url": f"https://www.instagram.com/reel/{sc}/",
                            "shortcode": sc,
                            "uploader": username,
                            "thumb_url": thumb,
                            "media_type": "video",
                            "slides_count": 1,
                            "format_options": [
                                {
                                    "label": "🎬 Best Video (Highest Quality)",
                                    "key": "video_best",
                                },
                                {
                                    "label": "🎞️ H.264 Compatibility Mode",
                                    "key": "video_h264",
                                },
                                {
                                    "label": "🎵 Audio Only (MP3 192kbps)",
                                    "key": "audio_mp3",
                                },
                            ],
                            "raw_media_items": [],
                        }
                        if v_list:
                            best_v = max(v_list, key=lambda x: int(x.get("width", 0)))
                            if best_v.get("url"):
                                single_item["raw_media_items"].append(
                                    {
                                        "url": best_v["url"],
                                        "ext": "mp4",
                                        "is_video": True,
                                    }
                                )

                    else:
                        single_item = {
                            "url": f"https://www.instagram.com/p/{sc}/",
                            "shortcode": sc,
                            "uploader": username,
                            "thumb_url": thumb,
                            "media_type": "photo",
                            "slides_count": 1,
                            "format_options": [
                                {
                                    "label": "🖼️ Original Resolution",
                                    "key": "best_single",
                                },
                                {
                                    "label": "🖼️ Compressed Web Size",
                                    "key": "720p_single",
                                },
                            ],
                            "raw_media_items": [],
                        }
                        if cands:
                            best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                            if best_s.get("url"):
                                single_item["raw_media_items"].append(
                                    {
                                        "url": best_s["url"],
                                        "ext": "jpg",
                                        "is_video": False,
                                    }
                                )

                    self.item_inspected.emit(single_item)
                    found_count += 1
                    batch_added += 1
                    self.progress_status.emit(
                        f"Found [{found_count}] items ({scope}) from @{username}"
                    )
                    time.sleep(0.015)

                more_available = bool(f_data.get("more_available", False))
                next_max_id = f_data.get("next_max_id")

                if (
                    not more_available
                    or not next_max_id
                    or next_max_id == max_id
                    or batch_added == 0
                ):
                    break

                max_id = next_max_id
                page_num += 1
                time.sleep(0.2)

        return found_count

    def _fetch_all_reels_web(
        self, username: str, opener: urllib.request.OpenerDirector
    ) -> int:
        found_count = 0
        seen_codes = set()
        user_id = None
        headers = self._build_web_headers(
            f"https://www.instagram.com/{username}/reels/"
        )

        self.progress_status.emit(f"Connecting to @{username} reels...")
        try:
            profile_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
            req = urllib.request.Request(profile_url, headers=headers)
            with opener.open(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user_data = data.get("data", {}).get("user", {})
                if user_data:
                    user_id = user_data.get("id") or user_data.get("pk")
                    felix_edges = user_data.get("edge_felix_video_timeline", {}).get(
                        "edges", []
                    )
                    timeline_edges = user_data.get(
                        "edge_owner_to_timeline_media", {}
                    ).get("edges", [])

                    raw_nodes = [
                        e.get("node", {}) for e in (felix_edges + timeline_edges)
                    ]
                    for node in raw_nodes:
                        if self._is_cancelled:
                            break
                        sc = node.get("shortcode") or node.get("id")
                        if not sc or sc in seen_codes or sc in self.existing_shortcodes:
                            continue
                        seen_codes.add(sc)

                        thumb = node.get("display_url", "")
                        video_url = node.get("video_url", "")

                        single_item = {
                            "url": f"https://www.instagram.com/reel/{sc}/",
                            "shortcode": sc,
                            "uploader": username,
                            "thumb_url": thumb,
                            "media_type": "video",
                            "slides_count": 1,
                            "format_options": [
                                {
                                    "label": "🎬 Best Video (Highest Quality)",
                                    "key": "video_best",
                                },
                                {
                                    "label": "🎞️ H.264 Compatibility Mode",
                                    "key": "video_h264",
                                },
                                {
                                    "label": "🎵 Audio Only (MP3 192kbps)",
                                    "key": "audio_mp3",
                                },
                            ],
                            "raw_media_items": [],
                        }
                        if video_url:
                            single_item["raw_media_items"].append(
                                {
                                    "url": video_url,
                                    "ext": "mp4",
                                    "is_video": True,
                                }
                            )

                        self.item_inspected.emit(single_item)
                        found_count += 1
                        self.progress_status.emit(
                            f"Found [{found_count}] reels from @{username}"
                        )
                        time.sleep(0.015)
        except Exception as e:
            print(f"[DEBUG] web_profile_info initial fetch error: {e}")

        if user_id and not self._is_cancelled:
            max_id = None
            page_num = 2

            while not self._is_cancelled:
                self.progress_status.emit(
                    f"Fetching Reels batch {page_num} from @{username}..."
                )
                clips_url = "https://www.instagram.com/api/v1/clips/user/"
                post_params = {
                    "target_user_id": str(user_id),
                    "page_size": "50",
                }
                if max_id:
                    post_params["max_id"] = str(max_id)

                post_data = urllib.parse.urlencode(post_params).encode("utf-8")
                req = urllib.request.Request(
                    clips_url,
                    data=post_data,
                    headers={
                        **headers,
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    },
                )

                try:
                    with opener.open(req, timeout=12) as resp:
                        c_data = json.loads(resp.read().decode("utf-8"))
                except Exception as e:
                    print(f"[DEBUG] Web Clips Pagination Error on page {page_num}: {e}")
                    break

                raw_items = c_data.get("items", [])
                if not raw_items:
                    break

                batch_added = 0
                for entry in raw_items:
                    if self._is_cancelled:
                        break
                    post = entry.get("media", {}) if "media" in entry else entry
                    sc = post.get("code")
                    if not sc or sc in seen_codes or sc in self.existing_shortcodes:
                        continue
                    seen_codes.add(sc)

                    v_list = post.get("video_versions", [])
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    thumb = cands[0]["url"] if cands else ""

                    single_item = {
                        "url": f"https://www.instagram.com/reel/{sc}/",
                        "shortcode": sc,
                        "uploader": username,
                        "thumb_url": thumb,
                        "media_type": "video",
                        "slides_count": 1,
                        "format_options": [
                            {
                                "label": "🎬 Best Video (Highest Quality)",
                                "key": "video_best",
                            },
                            {
                                "label": "🎞️ H.264 Compatibility Mode",
                                "key": "video_h264",
                            },
                            {
                                "label": "🎵 Audio Only (MP3 192kbps)",
                                "key": "audio_mp3",
                            },
                        ],
                        "raw_media_items": [],
                    }

                    if v_list:
                        best_v = max(v_list, key=lambda x: int(x.get("width", 0)))
                        if best_v.get("url"):
                            single_item["raw_media_items"].append(
                                {
                                    "url": best_v["url"],
                                    "ext": "mp4",
                                    "is_video": True,
                                }
                            )

                    self.item_inspected.emit(single_item)
                    found_count += 1
                    batch_added += 1
                    self.progress_status.emit(
                        f"Found [{found_count}] reels from @{username}"
                    )
                    time.sleep(0.015)

                paging = c_data.get("paging_info", {})
                more_available = bool(
                    paging.get("more_available") or c_data.get("more_available", False)
                )
                next_max_id = (
                    paging.get("max_id")
                    or c_data.get("next_max_id")
                    or c_data.get("max_id")
                )

                if (
                    not more_available
                    or not next_max_id
                    or next_max_id == max_id
                    or batch_added == 0
                ):
                    break

                max_id = next_max_id
                page_num += 1
                time.sleep(0.2)

        return found_count

    def run(self) -> None:
        opener = get_cookie_opener(self.cookie_path)
        total_found = 0

        for idx, target in enumerate(self.targets, 1):
            if self._is_cancelled:
                break

            if isinstance(target, dict):
                raw_url = target.get("url", "")
                scope = target.get("scope", "all")
            else:
                raw_url = str(target)
                scope = "all"

            parsed = parse_instagram_url(raw_url)
            if not parsed:
                continue

            identifier = parsed["identifier"]
            media_id = parsed.get("media_id", 0)
            url_type = parsed["type"]
            username = parsed.get("username", "Instagram")
            clean_url = parsed["clean_url"]

            self.progress_status.emit(
                f"Inspecting [{idx}/{len(self.targets)}]: {identifier}"
            )

            # =========================================================================
            # Tier 0: Batch Routing (Stories / Reels / Profile Feed)
            # =========================================================================
            if url_type == "story_user":
                user_id = self._get_user_id(username, opener)
                count = 0
                if user_id:
                    count = self._fetch_user_stories_web(username, user_id, opener)
                total_found += count
                if count == 0:
                    self.progress_status.emit(
                        f"⚠️ ไม่พบ Stories ใน @{username} (หรือไม่มี Story ที่ยังไม่หมดอายุ)"
                    )
                continue

            elif url_type == "profile_reels":
                count = self._fetch_all_reels_web(username, opener)
                total_found += count
                if count == 0:
                    self.progress_status.emit(f"⚠️ ไม่พบคลิป Reels ใน @{username}")
                continue

            elif url_type == "profile_posts":
                count = self._fetch_profile_feed_web(username, opener, scope)
                total_found += count
                if count == 0:
                    self.progress_status.emit(f"⚠️ ไม่พบโพสต์ใน @{username}")
                continue

            # =========================================================================
            # Tier 1: Single Media Item via Mobile REST API
            # =========================================================================
            # Check if this single item already exists in the grid
            if identifier in self.existing_shortcodes:
                continue

            item_data = {
                "url": clean_url,
                "shortcode": identifier,
                "uploader": username,
                "thumb_url": "",
                "media_type": url_type,
                "slides_count": 1,
                "format_options": [],
                "raw_media_items": [],
            }

            api_data = None
            if media_id > 0:
                try:
                    req_url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
                    req = urllib.request.Request(
                        req_url,
                        headers={
                            "User-Agent": MOBILE_UA,
                            "X-IG-App-ID": IG_APP_ID,
                            "Accept": "*/*",
                        },
                    )
                    with opener.open(req, timeout=9) as resp:
                        api_data = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    pass

            if api_data and "items" in api_data and api_data["items"]:
                post = api_data["items"][0]
                user_info = post.get("user", {})
                item_data["uploader"] = user_info.get("username", item_data["uploader"])

                if url_type in ("story", "highlight"):
                    item_data["media_type"] = "story"
                    v_list = post.get("video_versions", [])
                    is_story_video = bool(
                        v_list or post.get("is_video") or post.get("media_type") == 2
                    )
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""

                    if is_story_video:
                        best_v = (
                            max(v_list, key=lambda x: int(x.get("width", 0)))
                            if v_list
                            else {}
                        )
                        vw = best_v.get("width", 1080)
                        vh = best_v.get("height", 1920)
                        item_data["format_options"] = [
                            {
                                "label": f"🎬 Story Video ({vw}x{vh} - Best)",
                                "key": "story_video",
                            },
                            {
                                "label": "🎵 Audio Only (MP3 192kbps)",
                                "key": "audio_mp3",
                            },
                        ]
                        if best_v.get("url"):
                            item_data["raw_media_items"].append(
                                {
                                    "url": best_v["url"],
                                    "ext": "mp4",
                                    "is_video": True,
                                }
                            )
                    else:
                        w = cands[0].get("width", 1080) if cands else 1080
                        h = cands[0].get("height", 1920) if cands else 1920
                        item_data["format_options"] = [
                            {
                                "label": f"🖼️ Story Photo ({w}x{h} - Original)",
                                "key": "story_photo",
                            }
                        ]
                        if cands:
                            best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                            item_data["raw_media_items"].append(
                                {
                                    "url": best_s["url"],
                                    "ext": "jpg",
                                    "is_video": False,
                                }
                            )

                elif "carousel_media" in post and isinstance(
                    post["carousel_media"], list
                ):
                    item_data["media_type"] = "carousel"
                    item_data["slides_count"] = len(post["carousel_media"])
                    first_slide = post["carousel_media"][0]
                    s_cands = first_slide.get("image_versions2", {}).get(
                        "candidates", []
                    )
                    item_data["thumb_url"] = s_cands[0]["url"] if s_cands else ""
                    best_first = (
                        max(s_cands, key=lambda x: int(x.get("width", 0)))
                        if s_cands
                        else {}
                    )
                    w = best_first.get("width", 1080)
                    h = best_first.get("height", 1080)

                    item_data["format_options"] = [
                        {
                            "label": f"⚡ Best Quality ({w}x{h} Max) - All {item_data['slides_count']} Items",
                            "key": "best_all",
                        },
                        {
                            "label": "🖼️ Photos Only (Extract images only)",
                            "key": "photos_only",
                        },
                    ]

                    for s in post["carousel_media"]:
                        v_list = s.get("video_versions", [])
                        if v_list or s.get("is_video") or s.get("media_type") == 2:
                            best_v = (
                                max(v_list, key=lambda x: int(x.get("width", 0)))
                                if v_list
                                else {}
                            )
                            if best_v.get("url"):
                                item_data["raw_media_items"].append(
                                    {
                                        "url": best_v["url"],
                                        "ext": "mp4",
                                        "is_video": True,
                                    }
                                )
                        else:
                            s_cands = s.get("image_versions2", {}).get("candidates", [])
                            if s_cands:
                                best_s = max(
                                    s_cands, key=lambda x: int(x.get("width", 0))
                                )
                                item_data["raw_media_items"].append(
                                    {
                                        "url": best_s["url"],
                                        "ext": "jpg",
                                        "is_video": False,
                                    }
                                )

                elif (
                    post.get("video_versions")
                    or post.get("is_video", False)
                    or url_type == "video"
                ):
                    item_data["media_type"] = "video"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    v_list = post.get("video_versions", [])
                    best_v = (
                        max(v_list, key=lambda x: int(x.get("width", 0)))
                        if v_list
                        else {}
                    )
                    vw = best_v.get("width", 1080)
                    vh = best_v.get("height", 1920)

                    item_data["format_options"] = [
                        {
                            "label": f"🎬 Best Video ({vw}x{vh} - Max Bitrate)",
                            "key": "video_best",
                        },
                        {"label": "🎞️ H.264 Compatibility Mode", "key": "video_h264"},
                        {"label": "🎵 Audio Only (MP3 192kbps)", "key": "audio_mp3"},
                    ]
                    if best_v.get("url"):
                        item_data["raw_media_items"].append(
                            {
                                "url": best_v["url"],
                                "ext": "mp4",
                                "is_video": True,
                            }
                        )

                else:
                    item_data["media_type"] = "photo"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    w = cands[0].get("width", 1080) if cands else 1080
                    h = cands[0].get("height", 1080) if cands else 1080

                    item_data["format_options"] = [
                        {
                            "label": f"🖼️ Original Resolution ({w}x{h})",
                            "key": "best_single",
                        },
                        {"label": "🖼️ Compressed Web Size", "key": "720p_single"},
                    ]
                    if cands:
                        best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                        item_data["raw_media_items"].append(
                            {
                                "url": best_s["url"],
                                "ext": "jpg",
                                "is_video": False,
                            }
                        )

            # =========================================================================
            # Tier 2: Single Fallback via yt-dlp
            # =========================================================================
            if not item_data["format_options"]:
                try:
                    ydl_opts = {
                        "quiet": True,
                        "logger": SilentLogger(),
                        "http_headers": {
                            "User-Agent": DESKTOP_UA,
                            "X-IG-App-ID": IG_APP_ID,
                        },
                    }
                    if self.cookie_path and os.path.exists(self.cookie_path):
                        ydl_opts["cookiefile"] = self.cookie_path

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(clean_url, download=False)
                        if info:
                            item_data["uploader"] = info.get(
                                "uploader", item_data["uploader"]
                            )
                            item_data["thumb_url"] = info.get("thumbnail", "")
                            is_vid = info.get("vcodec") != "none" or url_type in (
                                "story",
                                "video",
                            )
                            item_data["media_type"] = (
                                "story"
                                if url_type == "story"
                                else ("video" if is_vid else "photo")
                            )

                            if is_vid:
                                item_data["format_options"] = [
                                    {
                                        "label": "🎬 Best Video (Highest Quality)",
                                        "key": "video_best",
                                    },
                                    {
                                        "label": "🎞️ H.264 Compatibility Mode",
                                        "key": "video_h264",
                                    },
                                    {
                                        "label": "🎵 Audio Only (MP3 192kbps)",
                                        "key": "audio_mp3",
                                    },
                                ]
                            else:
                                item_data["format_options"] = [
                                    {
                                        "label": "🖼️ Best Photo Resolution",
                                        "key": "best_single",
                                    },
                                ]
                except Exception as e:
                    print(f"[DEBUG] yt-dlp Single Fallback Error: {e}")
                    is_reel = url_type in ("video", "story") or "/reel/" in clean_url
                    item_data["media_type"] = "video" if is_reel else "photo"
                    if is_reel:
                        item_data["format_options"] = [
                            {
                                "label": "🎬 Best Video (Auto-Engine)",
                                "key": "video_best",
                            },
                            {"label": "🎞️ H.264 Mode", "key": "video_h264"},
                            {"label": "🎵 Audio MP3", "key": "audio_mp3"},
                        ]
                    else:
                        item_data["format_options"] = [
                            {
                                "label": "⚡ Best Quality (Auto-Engine)",
                                "key": "best_single",
                            }
                        ]

            if item_data["format_options"]:
                self.item_inspected.emit(item_data)
                total_found += 1

            time.sleep(0.05)

        self.finished_inspection.emit(total_found)
