"""
core/download_worker.py - Background QThread executing Direct CDN streaming & yt-dlp video pipelines.
"""

import os
import time
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp

from config.constants import DESKTOP_UA, IG_APP_ID
from utils.file_utils import get_ffmpeg_dir, sanitize_filename
from utils.logger import SilentLogger


class GridDownloadWorker(QThread):
    progress_signal = pyqtSignal(dict)
    item_started = pyqtSignal(int, int, str)
    item_finished = pyqtSignal(int, bool, str, str)
    all_finished = pyqtSignal(int, int, bool)

    def __init__(self, card_data_list: list[dict], save_path: str, cookie_path: str | None):
        super().__init__()
        self.items = card_data_list
        self.save_path = save_path
        self.cookie_path = cookie_path
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def _download_file(self, url: str, dest_path: str) -> bool:
        """ดาวน์โหลดไฟล์แบบ Chunked พร้อมส่ง Progress Bar Signal อย่างต่อเนื่อง"""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": DESKTOP_UA,
                    "Referer": "https://www.instagram.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp, open(dest_path, "wb") as f:
                total_bytes = int(resp.info().get("Content-Length", 0))
                downloaded_bytes = 0
                block_size = 64 * 1024
                start_time = time.time()

                while True:
                    if self._is_cancelled:
                        return False
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    f.write(chunk)

                    elapsed = time.time() - start_time
                    speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0
                    pct = (downloaded_bytes / total_bytes * 100) if total_bytes > 0 else 100.0

                    self.progress_signal.emit({
                        "percent": pct,
                        "downloaded": downloaded_bytes,
                        "total": total_bytes,
                        "speed": speed,
                        "eta": (((total_bytes - downloaded_bytes) / speed) if speed > 0 and total_bytes > downloaded_bytes else 0),
                    })
            return True
        except Exception:
            return False

    def run(self) -> None:
        os.makedirs(self.save_path, exist_ok=True)
        ffmpeg_dir = get_ffmpeg_dir()
        total = len(self.items)
        success_count = 0
        fail_count = 0

        def progress_hook(d: dict) -> None:
            if self._is_cancelled:
                raise Exception("Cancelled")
            if d.get("status") == "downloading":
                total_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                down_b = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0
                pct = (down_b / total_b * 100) if total_b else 0.0
                self.progress_signal.emit({
                    "percent": pct,
                    "downloaded": down_b,
                    "total": total_b,
                    "speed": speed,
                    "eta": eta,
                })

        for idx, item in enumerate(self.items, 1):
            if self._is_cancelled:
                break

            shortcode = sanitize_filename(item["shortcode"])
            uploader = sanitize_filename(item.get("uploader", "Instagram"))
            url = item["url"]
            chosen_format = item["selected_format"]
            self.item_started.emit(idx - 1, total, shortcode)

            ok = False
            current_date = time.strftime("%Y%m%d")
            final_saved_path = self.save_path

            # 1. Direct Media Items (Carousels, Photos, Stories)
            direct_format_keys = (
                "best_all",
                "photos_only",
                "best_single",
                "720p_single",
                "story_video",
                "story_photo",
                "1080p_all",
                "auto_best",
            )

            if item.get("raw_media_items") and chosen_format in direct_format_keys:
                img_list = item["raw_media_items"]
                if chosen_format == "photos_only":
                    img_list = [m for m in img_list if not m.get("is_video")]

                success_slides = 0
                for s_idx, raw_media in enumerate(img_list, 1):
                    if self._is_cancelled:
                        break

                    ext = raw_media.get("ext", "jpg")
                    if len(img_list) > 1:
                        fn = f"{uploader}_{current_date}_{shortcode}_{s_idx:02d}.{ext}"
                    else:
                        fn = f"{uploader}_{current_date}_{shortcode}.{ext}"

                    fp = os.path.join(self.save_path, fn)
                    if self._download_file(raw_media["url"], fp):
                        success_slides += 1
                        final_saved_path = fp

                ok = success_slides > 0

            # 2. yt-dlp Engine (Videos / Reels / Audio MP3 / Fallback)
            if not ok and not self._is_cancelled:
                ydl_opts = {
                    "paths": {"home": self.save_path},
                    "windowsfilenames": True,
                    "trim_file_name": 120,
                    "http_headers": {
                        "User-Agent": DESKTOP_UA,
                        "X-IG-App-ID": IG_APP_ID,
                    },
                    "outtmpl": "%(uploader,uploader_id|Instagram)s_%(upload_date>%Y%m%d,Unknown)s_%(id)s.%(ext)s",
                    "progress_hooks": [progress_hook],
                    "logger": SilentLogger(),
                    "quiet": True,
                    "merge_output_format": "mp4",
                }
                if ffmpeg_dir:
                    ydl_opts["ffmpeg_location"] = ffmpeg_dir
                if self.cookie_path and os.path.exists(self.cookie_path):
                    ydl_opts["cookiefile"] = self.cookie_path

                if chosen_format == "audio_mp3":
                    ydl_opts["format"] = "best"
                    ydl_opts["postprocessors"] = [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ]
                elif chosen_format == "video_h264":
                    ydl_opts["format"] = "bestvideo*+bestaudio/best"
                    ydl_opts["format_sort"] = ["vcodec:h264", "res", "fps"]
                else:
                    ydl_opts["format"] = "bestvideo*+bestaudio/best"
                    ydl_opts["format_sort"] = ["res:1080", "res", "vbr", "fps", "size"]

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            final_saved_path = ydl.prepare_filename(info)
                    ok = True
                except Exception:
                    ok = False

            if ok:
                success_count += 1
                self.item_finished.emit(idx - 1, True, "Done", final_saved_path)
            else:
                if not self._is_cancelled:
                    fail_count += 1
                    self.item_finished.emit(idx - 1, False, "Failed", "")

            time.sleep(0.3)

        self.all_finished.emit(success_count, fail_count, self._is_cancelled)