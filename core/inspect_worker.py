"""
core/inspect_worker.py - Background QThread for inspecting URL payloads via REST API & yt-dlp fallback.
"""

import json
import os
import time
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

    def __init__(self, urls: list[str], cookie_path: str | None):
        super().__init__()
        self.urls = urls
        self.cookie_path = cookie_path
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        opener = get_cookie_opener(self.cookie_path)
        found_count = 0

        for idx, raw_url in enumerate(self.urls, 1):
            if self._is_cancelled:
                break

            parsed = parse_instagram_url(raw_url)
            if not parsed:
                continue

            identifier = parsed["identifier"]
            media_id = parsed["media_id"]
            url_type = parsed["type"]
            self.progress_status.emit(f"Inspecting [{idx}/{len(self.urls)}]: {identifier}")

            item_data = {
                "url": parsed["clean_url"],
                "shortcode": identifier,
                "uploader": parsed.get("username", "Instagram"),
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

                # Case 1: Story / Highlight
                if url_type in ("story", "highlight"):
                    item_data["media_type"] = "story"
                    v_list = post.get("video_versions", [])
                    is_story_video = bool(v_list or post.get("is_video") or post.get("media_type") == 2)
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""

                    if is_story_video:
                        best_v = max(v_list, key=lambda x: int(x.get("width", 0))) if v_list else {}
                        vw = best_v.get("width", 1080)
                        vh = best_v.get("height", 1920)
                        item_data["format_options"] = [
                            {"label": f"🎬 Story Video ({vw}x{vh} - Best)", "key": "story_video"},
                            {"label": "🎵 Audio Only (MP3 192kbps)", "key": "audio_mp3"},
                        ]
                        if best_v.get("url"):
                            item_data["raw_media_items"].append({"url": best_v["url"], "ext": "mp4", "is_video": True})
                    else:
                        w = cands[0].get("width", 1080) if cands else 1080
                        h = cands[0].get("height", 1920) if cands else 1920
                        item_data["format_options"] = [
                            {"label": f"🖼️ Story Photo ({w}x{h} - Original)", "key": "story_photo"}
                        ]
                        if cands:
                            best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                            item_data["raw_media_items"].append({"url": best_s["url"], "ext": "jpg", "is_video": False})

                # Case 2: Carousel อัลบั้ม
                elif "carousel_media" in post and isinstance(post["carousel_media"], list):
                    item_data["media_type"] = "carousel"
                    item_data["slides_count"] = len(post["carousel_media"])
                    first_slide = post["carousel_media"][0]
                    cands = first_slide.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""

                    item_data["format_options"] = [
                        {
                            "label": f"⚡ Best Quality (Photos & Videos) - All {item_data['slides_count']} Items",
                            "key": "best_all",
                        },
                        {"label": "🖼️ Photos Only (Extract images only)", "key": "photos_only"},
                    ]

                    for s in post["carousel_media"]:
                        v_list = s.get("video_versions", [])
                        if v_list or s.get("is_video") or s.get("media_type") == 2:
                            best_v = max(v_list, key=lambda x: int(x.get("width", 0))) if v_list else {}
                            if best_v.get("url"):
                                item_data["raw_media_items"].append({"url": best_v["url"], "ext": "mp4", "is_video": True})
                        else:
                            s_cands = s.get("image_versions2", {}).get("candidates", [])
                            if s_cands:
                                best_s = max(s_cands, key=lambda x: int(x.get("width", 0)))
                                item_data["raw_media_items"].append({"url": best_s["url"], "ext": "jpg", "is_video": False})

                # Case 3: Video / Reel
                elif post.get("video_versions") or post.get("is_video", False) or url_type == "video":
                    item_data["media_type"] = "video"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    v_list = post.get("video_versions", [])
                    best_v = max(v_list, key=lambda x: int(x.get("width", 0))) if v_list else {}
                    vw = best_v.get("width", 1080)
                    vh = best_v.get("height", 1920)

                    item_data["format_options"] = [
                        {"label": f"🎬 Best Video ({vw}x{vh} - Max Bitrate)", "key": "video_best"},
                        {"label": "🎞️ H.264 Compatibility Mode", "key": "video_h264"},
                        {"label": "🎵 Audio Only (MP3 192kbps)", "key": "audio_mp3"},
                    ]
                    if best_v.get("url"):
                        item_data["raw_media_items"].append({"url": best_v["url"], "ext": "mp4", "is_video": True})

                # Case 4: Single Photo
                else:
                    item_data["media_type"] = "photo"
                    cands = post.get("image_versions2", {}).get("candidates", [])
                    item_data["thumb_url"] = cands[0]["url"] if cands else ""
                    w = cands[0].get("width", 1080) if cands else 1080
                    h = cands[0].get("height", 1080) if cands else 1080

                    item_data["format_options"] = [
                        {"label": f"🖼️ Original Resolution ({w}x{h})", "key": "best_single"},
                        {"label": "🖼️ Compressed Web Size", "key": "720p_single"},
                    ]
                    if cands:
                        best_s = max(cands, key=lambda x: int(x.get("width", 0)))
                        item_data["raw_media_items"].append({"url": best_s["url"], "ext": "jpg", "is_video": False})

            # Fallback ผ่าน yt-dlp Metadata
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
                        info = ydl.extract_info(raw_url, download=False)
                        if info:
                            item_data["uploader"] = info.get("uploader", item_data["uploader"])
                            item_data["thumb_url"] = info.get("thumbnail", "")
                            is_vid = info.get("vcodec") != "none" or url_type in ("story", "video")
                            item_data["media_type"] = "story" if url_type == "story" else ("video" if is_vid else "photo")

                            if is_vid:
                                item_data["format_options"] = [
                                    {"label": "🎬 Best Video (Highest Quality)", "key": "video_best"},
                                    {"label": "🎞️ H.264 Compatibility Mode", "key": "video_h264"},
                                    {"label": "🎵 Audio Only (MP3 192kbps)", "key": "audio_mp3"},
                                ]
                            else:
                                item_data["format_options"] = [
                                    {"label": "🖼️ Best Photo Resolution", "key": "best_single"},
                                ]
                except Exception:
                    is_reel = url_type in ("video", "story") or "/reel/" in raw_url
                    item_data["media_type"] = "video" if is_reel else "photo"
                    if is_reel:
                        item_data["format_options"] = [
                            {"label": "🎬 Best Video (Auto-Engine)", "key": "video_best"},
                            {"label": "🎞️ H.264 Mode", "key": "video_h264"},
                            {"label": "🎵 Audio MP3", "key": "audio_mp3"},
                        ]
                    else:
                        item_data["format_options"] = [
                            {"label": "⚡ Best Quality (Auto-Engine)", "key": "best_single"}
                        ]

            self.item_inspected.emit(item_data)
            found_count += 1
            time.sleep(0.2)

        self.finished_inspection.emit(found_count)