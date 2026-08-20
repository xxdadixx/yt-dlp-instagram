"""
core/download_worker.py - Multi-threaded Media Download Worker with Direct CDN Streaming,
FFmpeg Post-processing, and fallback to yt-dlp.
"""

import os
import re
import subprocess
import time
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal
import yt_dlp

from config.constants import DESKTOP_UA, IG_APP_ID
from core.cookie_manager import get_cookie_opener
from utils.file_utils import get_ffmpeg_dir
from utils.logger import SilentLogger


def sanitize_filename(name: str) -> str:
    """Sanitize string for safe cross-platform filenames."""
    clean = re.sub(r'[\\/*?:"<>|]', "", str(name))
    return clean.strip().replace(" ", "_") or "media"


class GridDownloadWorker(QThread):
    item_started = pyqtSignal(int, int, str)
    item_finished = pyqtSignal(int, bool, str, str)
    progress_signal = pyqtSignal(dict)
    all_finished = pyqtSignal(int, int, bool)

    def __init__(self, target_cards: list, save_dir: str, cookie_path: str | None):
        super().__init__()
        self.target_cards = target_cards
        self.save_dir = save_dir
        self.cookie_path = cookie_path
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def _get_unique_filepath(self, folder: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(folder, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{base}_{counter}{ext}")
            counter += 1
        return candidate

    def _download_stream_direct(
        self, url: str, target_path: str, opener: urllib.request.OpenerDirector
    ) -> bool:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": DESKTOP_UA,
                    "Referer": "https://www.instagram.com/",
                },
            )
            with opener.open(req, timeout=20) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                start_time = time.time()
                last_update = 0.0

                with open(target_path, "wb") as f:
                    while not self._is_cancelled:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_update > 0.1:
                            last_update = now
                            elapsed = now - start_time or 0.001
                            speed = downloaded / elapsed
                            percent = (
                                (downloaded / total_size * 100)
                                if total_size > 0
                                else 0.0
                            )
                            self.progress_signal.emit(
                                {
                                    "downloaded": downloaded,
                                    "total": total_size,
                                    "speed": speed,
                                    "percent": percent,
                                }
                            )

                if self._is_cancelled:
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    return False
                return True
        except Exception as e:
            print(f"[DEBUG] Direct download error: {e}")
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            return False

    def _convert_to_mp3(self, input_file: str) -> str | None:
        output_file = os.path.splitext(input_file)[0] + ".mp3"
        ffmpeg_dir = get_ffmpeg_dir()
        ffmpeg_bin = os.path.join(
            ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        )

        cmd = [
            ffmpeg_bin if os.path.exists(ffmpeg_bin) else "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            "192k",
            output_file,
        ]

        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                startupinfo=startupinfo,
            )
            if os.path.exists(input_file) and input_file != output_file:
                os.remove(input_file)
            return output_file
        except Exception as e:
            print(f"[DEBUG] FFmpeg conversion failed: {e}")
            return input_file

    def run(self) -> None:
        os.makedirs(self.save_dir, exist_ok=True)
        opener = get_cookie_opener(self.cookie_path)
        total_items = len(self.target_cards)
        success_count = 0
        fail_count = 0

        for idx, card in enumerate(self.target_cards):
            if self._is_cancelled:
                break

            if hasattr(card, "data"):
                data = card.data
                selected_format = (
                    card.get_selected_format()
                    if hasattr(card, "get_selected_format")
                    else "best_all"
                )
            else:
                data = card
                selected_format = data.get("selected_format", "best_all")

            shortcode = sanitize_filename(data.get("shortcode", f"media_{idx}"))
            uploader = sanitize_filename(data.get("uploader", "instagram"))
            raw_media = data.get("raw_media_items", [])
            web_url = data.get("url", "")

            self.item_started.emit(idx, total_items, shortcode)

            item_success = False
            first_saved_path = ""

            # Strategy 1: Direct CDN Streaming
            if raw_media:
                all_slides_ok = True
                for s_idx, m in enumerate(raw_media, 1):
                    if self._is_cancelled:
                        all_slides_ok = False
                        break

                    ext = m.get("ext", "mp4" if m.get("is_video") else "jpg")
                    suffix = f"_{s_idx}" if len(raw_media) > 1 else ""
                    filename = f"{uploader}_{shortcode}{suffix}.{ext}"
                    filepath = self._get_unique_filepath(self.save_dir, filename)

                    ok = self._download_stream_direct(m["url"], filepath, opener)
                    if ok:
                        if selected_format == "audio_mp3" and ext == "mp4":
                            filepath = self._convert_to_mp3(filepath) or filepath
                        if not first_saved_path:
                            first_saved_path = filepath
                    else:
                        all_slides_ok = False
                        break

                if all_slides_ok and first_saved_path:
                    item_success = True

            # Strategy 2: yt-dlp Engine Fallback
            if not item_success and not self._is_cancelled and web_url:
                try:
                    out_tmpl = os.path.join(self.save_dir, f"{uploader}_%(id)s.%(ext)s")
                    ydl_opts = {
                        "outtmpl": out_tmpl,
                        "quiet": True,
                        "logger": SilentLogger(),
                        "http_headers": {
                            "User-Agent": DESKTOP_UA,
                            "X-IG-App-ID": IG_APP_ID,
                        },
                    }
                    if selected_format == "audio_mp3":
                        ydl_opts["format"] = "bestaudio/best"
                        ydl_opts["postprocessors"] = [
                            {
                                "key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": "192",
                            }
                        ]

                    if self.cookie_path and os.path.exists(self.cookie_path):
                        ydl_opts["cookiefile"] = self.cookie_path

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(web_url, download=True)
                        if info:
                            first_saved_path = ydl.prepare_filename(info)
                            item_success = True
                except Exception as e:
                    print(f"[DEBUG] yt-dlp Download Fallback Error: {e}")

            if item_success:
                success_count += 1
                self.item_finished.emit(idx, True, "Done", first_saved_path)
            else:
                fail_count += 1
                self.item_finished.emit(idx, False, "Failed", "")

            time.sleep(0.05)

        self.all_finished.emit(success_count, fail_count, self._is_cancelled)
