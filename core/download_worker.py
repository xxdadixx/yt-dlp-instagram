"""
core/download_worker.py - High-throughput sequential media download worker
with monotonic chronological ordering (Most Recent -> Oldest), connection pooling,
256 KB streaming buffers, and atomic file replacement.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import cast, override

import requests
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from config.constants import DEFAULT_USER_AGENT, IG_APP_ID
from core.parser import shortcode_to_id
from utils.file_utils import sanitize_filesystem_name

logger = logging.getLogger("DownloadWorker")


def sanitize_filename(value: str, fallback: str = "media") -> str:
    """Sanitizes filename strings for safe filesystem operations."""
    cleaned = sanitize_filesystem_name(str(value), max_length=64)
    return cleaned if cleaned else fallback


def extract_chronological_key(item_data: object) -> int:
    """Extracts monotonic integer timestamp or snowflake ID from media payload (higher = newer)."""
    if not isinstance(item_data, dict):
        return 0

    payload = cast(dict[str, object], item_data)

    ts = (
        payload.get("taken_at_timestamp")
        or payload.get("taken_at")
        or payload.get("timestamp")
        or payload.get("date")
    )
    if ts is not None:
        try:
            return int(str(ts))
        except (ValueError, TypeError):
            pass

    raw_id = payload.get("id") or payload.get("pk") or payload.get("media_id")
    if raw_id is not None:
        try:
            clean_id = str(raw_id).split("_")[0]
            val = int(clean_id)
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass

    shortcode = payload.get("shortcode") or payload.get("code")
    if isinstance(shortcode, str) and shortcode:
        decoded = shortcode_to_id(shortcode)
        if decoded is not None and decoded > 0:
            return decoded

    return 0


def extract_queue_sort_key(item_data: object) -> tuple[int, int, int]:
    """Extracts a composite monotonic sort key prioritizing URL queue order:
    (target_index, -chronological_timestamp_or_snowflake, sub_index).
    """
    if not isinstance(item_data, dict):
        return (0, 0, 0)

    payload = cast(dict[str, object], item_data)
    try:
        target_idx = int(str(payload.get("target_index") or 0))
    except (ValueError, TypeError):
        target_idx = 0

    chrono_key = extract_chronological_key(item_data)

    try:
        sub_idx = int(str(payload.get("sub_index") or 0))
    except (ValueError, TypeError):
        sub_idx = 0

    return (target_idx, -chrono_key, sub_idx)


class DownloadWorker(QThread):
    progress: pyqtSignal = pyqtSignal(int)
    item_started: pyqtSignal = pyqtSignal(str)
    item_finished: pyqtSignal = pyqtSignal(str, bool)
    finished: pyqtSignal = pyqtSignal(int)

    items: list[dict[str, object]]
    save_folder: str
    cookie_file: str | None
    cookie_str: str | None
    quality_preset: str
    _is_cancelled: bool
    session: requests.Session

    sanitize_filename = staticmethod(sanitize_filename)

    def __init__(
        self,
        items: list[dict[str, object]] | None = None,
        save_folder: str = "downloads",
        cookie_file: str | None = None,
        cookie_str: str | None = None,
        quality_preset: str = "best_video",
        queue_items: list[dict[str, object]] | None = None,
        output_directory: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        raw_items: list[dict[str, object]] = list(items or queue_items or [])

        # Strict Queue Order: Primary = URL Input Order (0 -> N); Secondary = Monotonic Chronological Order
        raw_items.sort(key=extract_queue_sort_key)
        self.items = raw_items

        self.save_folder = os.path.abspath(
            save_folder or output_directory or "downloads"
        )
        self.cookie_file = (
            cookie_file if cookie_file and os.path.exists(cookie_file) else None
        )
        self.cookie_str = cookie_str
        self.quality_preset = quality_preset
        self._is_cancelled = False
        os.makedirs(self.save_folder, exist_ok=True)

        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            pool_connections=16,
            pool_maxsize=16,
            max_retries=retries,
            pool_block=False,
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        headers: dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "X-IG-App-ID": IG_APP_ID,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str

        self.session.headers.update(headers)

    @property
    def is_cancelled(self) -> bool:
        """Thread-safe query for cooperative cancellation status."""
        return self._is_cancelled

    def cancel(self) -> None:
        """Signals the worker to abort active downloads cooperatively and tears down open connections."""
        self._is_cancelled = True
        self.requestInterruption()
        try:
            self.session.close()
        except Exception as exc:
            logger.debug("Exception encountered while closing worker session: %s", exc)

    def _build_filename(
        self,
        item_or_username: dict[str, object] | str,
        shortcode: str | None = None,
        ext: str = "mp4",
        slide_index: int | None = None,
    ) -> str:
        if isinstance(item_or_username, dict):
            raw_user = item_or_username.get("username") or "instagram"
            raw_code = (
                item_or_username.get("shortcode")
                or item_or_username.get("id")
                or "media"
            )
            slide_idx = item_or_username.get("slide_index") or item_or_username.get(
                "index"
            )
        else:
            raw_user = item_or_username or "instagram"
            raw_code = shortcode or "media"
            slide_idx = slide_index

        clean_user = sanitize_filename(str(raw_user), fallback="instagram")
        clean_code = sanitize_filename(str(raw_code), fallback="media")
        clean_ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower() or "mp4"

        if slide_idx is not None:
            return f"{clean_user}_{clean_code}_{slide_idx}.{clean_ext}"
        return f"{clean_user}_{clean_code}.{clean_ext}"

    def _build_filepath(
        self,
        item_or_username: dict[str, object] | str,
        shortcode: str | None = None,
        ext: str = "mp4",
        slide_index: int | None = None,
    ) -> str:
        filename = self._build_filename(
            item_or_username=item_or_username,
            shortcode=shortcode,
            ext=ext,
            slide_index=slide_index,
        )
        return os.path.join(self.save_folder, filename)

    def _get_download_headers(self, url: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
        }
        is_cdn = any(domain in url for domain in ("cdninstagram.com", "fbcdn.net"))
        if not is_cdn and self.cookie_str:
            headers["Cookie"] = self.cookie_str
        return headers

    def _download_direct_stream(
        self, url: str, target_path: str, index: int, total_items: int
    ) -> str:
        """Streams media chunk-by-chunk with dynamic Content-Type extension validation to prevent container mismatch."""
        chunk_size = 256 * 1024  # 256 KB streaming chunks
        req_headers = self._get_download_headers(url)
        completed_successfully = False
        actual_target_path = target_path

        try:
            with self.session.get(
                url,
                headers=req_headers,
                stream=True,
                timeout=(6.0, 30.0),
                verify=True,
            ) as response:
                response.raise_for_status()

                # Content-Type sniffing: Reconcile payload MIME type with file extension
                content_type = response.headers.get("Content-Type", "").lower()
                base_stem, ext = os.path.splitext(actual_target_path)

                if "image/" in content_type:
                    correct_ext = (
                        ".jpg"
                        if "jpeg" in content_type or "jpg" in content_type
                        else (".webp" if "webp" in content_type else ".png")
                    )
                    if ext.lower() in (".mp4", ".m4v", ".mov", ".mkv"):
                        logger.info(
                            "MIME-type sniff corrected container: [%s] -> [%s] (%s)",
                            ext,
                            correct_ext,
                            content_type,
                        )
                        actual_target_path = base_stem + correct_ext
                elif "video/" in content_type:
                    if ext.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                        logger.info(
                            "MIME-type sniff corrected container: [%s] -> [.mp4] (%s)",
                            ext,
                            content_type,
                        )
                        actual_target_path = base_stem + ".mp4"

                temp_path = f"{actual_target_path}.part"

                try:
                    total_size = int(response.headers.get("content-length", 0) or 0)
                except (ValueError, TypeError):
                    total_size = 0

                downloaded_bytes = 0
                last_signal_time = 0.0

                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if self._is_cancelled or self.isInterruptionRequested():
                            raise InterruptedError("Download cancelled by user.")

                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)

                            current_time = time.time()
                            if (
                                current_time - last_signal_time >= 0.05
                                or downloaded_bytes == total_size
                            ):
                                last_signal_time = current_time
                                item_percent = (
                                    (downloaded_bytes / total_size)
                                    if total_size > 0
                                    else 0.0
                                )
                                overall_percent = int(
                                    ((index + item_percent) / total_items) * 100
                                )
                                self.progress.emit(min(overall_percent, 99))

            if self._is_cancelled or self.isInterruptionRequested():
                raise InterruptedError("Download cancelled by user.")

            os.replace(temp_path, actual_target_path)
            completed_successfully = True
            return actual_target_path

        except (requests.exceptions.RequestException, OSError, InterruptedError) as exc:
            if not self._is_cancelled:
                logger.warning(
                    "Stream failed for target %s: %s", actual_target_path, exc
                )
            return ""

        finally:
            if (
                not completed_successfully
                and "temp_path" in locals()
                and os.path.exists(temp_path)
            ):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _download_via_ytdlp(
        self,
        item: dict[str, object],
        username: str,
        shortcode: str,
        index: int,
        total_items: int,
    ) -> str:
        if yt_dlp is None or self._is_cancelled or self.isInterruptionRequested():
            return ""

        raw_url = str(item.get("webpage_url") or item.get("url") or "")
        is_cdn = any(cdn in raw_url for cdn in ("cdninstagram.com", "fbcdn.net"))

        if raw_url and "instagram.com" in raw_url and not is_cdn:
            url = raw_url
        elif shortcode and shortcode != "media":
            url = f"https://www.instagram.com/p/{shortcode}/"
        else:
            url = raw_url or str(item.get("download_url") or "")

        clean_user = sanitize_filename(username, fallback="instagram_user")
        clean_code = sanitize_filename(shortcode, fallback="media")
        is_carousel = "carousel" in str(item.get("media_type", "")).lower() or bool(
            item.get("carousel_count")
        )

        if is_carousel:
            out_template = os.path.join(
                self.save_folder,
                f"{clean_user}_{clean_code}_%(playlist_index)s.%(ext)s",
            )
        else:
            out_template = os.path.join(
                self.save_folder, f"{clean_user}_{clean_code}.%(ext)s"
            )

        last_progress_time = 0.0

        def ytdlp_hook(d: dict[str, object]) -> None:
            nonlocal last_progress_time
            if self._is_cancelled or self.isInterruptionRequested():
                if yt_dlp is not None:
                    raise yt_dlp.utils.DownloadCancelled("Download cancelled by user.")
                raise InterruptedError("Download cancelled by user.")

            if d.get("status") == "downloading":
                now = time.time()
                if now - last_progress_time >= 0.05:
                    last_progress_time = now
                    try:
                        raw_total = (
                            d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        )
                        total = int(float(str(raw_total)))
                    except (ValueError, TypeError):
                        total = 0

                    try:
                        raw_downloaded = d.get("downloaded_bytes") or 0
                        downloaded = int(float(str(raw_downloaded)))
                    except (ValueError, TypeError):
                        downloaded = 0

                    item_percent = (downloaded / total) if total > 0 else 0.0
                    overall_percent = int(((index + item_percent) / total_items) * 100)
                    self.progress.emit(min(overall_percent, 99))

        ydl_opts: dict[str, object] = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [ytdlp_hook],
            "nocheckcertificate": False,
            "buffersize": 256 * 1024,
            "http_chunk_size": 10485760,
            "concurrent_fragment_downloads": 4,
            "retries": 3,
            "fragment_retries": 3,
        }

        from utils.file_utils import get_ffmpeg_dir

        ffmpeg_bin_dir = get_ffmpeg_dir()
        if ffmpeg_bin_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin_dir

        if self.quality_preset == "audio_only":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        elif self.quality_preset == "720p":
            ydl_opts["format"] = (
                "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
            )
        elif self.quality_preset == "1080p":
            ydl_opts["format"] = (
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
            )

        if self.cookie_file and os.path.exists(self.cookie_file):
            ydl_opts["cookiefile"] = self.cookie_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_obj: object = ydl.extract_info(url, download=True)
                if not info_obj or not isinstance(info_obj, dict):
                    return ""
                info = cast(dict[str, object], info_obj)
                entries_obj = info.get("entries")
                if isinstance(entries_obj, list) and entries_obj:
                    first_entry = entries_obj[0]
                    target_entry = (
                        first_entry if isinstance(first_entry, dict) else info
                    )
                else:
                    target_entry = info

                prepared = str(ydl.prepare_filename(target_entry))

                # If postprocessor converted audio, verify the resulting .mp3 container
                if self.quality_preset == "audio_only":
                    mp3_path = os.path.splitext(prepared)[0] + ".mp3"
                    if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
                        return mp3_path

                if os.path.isfile(prepared) and os.path.getsize(prepared) > 0:
                    return prepared

                # Fallback directory scan for generated file matching base stem
                stem = os.path.splitext(prepared)[0]
                parent_dir = os.path.dirname(prepared)
                if os.path.isdir(parent_dir):
                    for fname in os.listdir(parent_dir):
                        full_p = os.path.join(parent_dir, fname)
                        if (
                            full_p.startswith(stem)
                            and os.path.isfile(full_p)
                            and os.path.getsize(full_p) > 0
                        ):
                            return full_p

                return prepared
        except Exception as exc:
            if not self._is_cancelled:
                logger.warning("yt-dlp download failed for %s: %s", url, exc)
            return ""

    def _process_single_item(
        self, item: dict[str, object], index: int, total_items: int
    ) -> str:
        if self._is_cancelled:
            return ""

        username = str(item.get("username") or item.get("uploader") or "instagram_user")
        shortcode = str(item.get("shortcode") or item.get("id") or f"media_{index}")

        # 1. Multi-Item Carousel -> Direct Stream Each Slide
        raw_slides: object = item.get("slides")
        if isinstance(raw_slides, list) and len(raw_slides) > 0:
            slides_list: list[dict[str, object]] = [
                cast(dict[str, object], s)
                for s in cast(list[object], raw_slides)
                if isinstance(s, dict)
            ]
            saved_any = False
            last_path = ""
            for s_idx, slide in enumerate(slides_list, start=1):
                if self._is_cancelled:
                    break
                slide_url = str(
                    slide.get("download_url")
                    or slide.get("video_url")
                    or slide.get("thumbnail_url")
                    or ""
                )
                is_vid = bool(slide.get("is_video"))
                ext = "mp4" if is_vid else "jpg"
                slide_path = self._build_filepath(
                    item_or_username=username,
                    shortcode=shortcode,
                    slide_index=s_idx,
                    ext=ext,
                )

                if slide_url.startswith(("http://", "https://")):
                    try:
                        out = self._download_direct_stream(
                            slide_url, slide_path, index, total_items
                        )
                        if out and os.path.isfile(out) and os.path.getsize(out) > 0:
                            saved_any = True
                            last_path = out
                    except Exception as s_err:
                        if self._is_cancelled:
                            break
                        logger.debug("Slide %d direct stream failed: %s", s_idx, s_err)

            if self._is_cancelled:
                return ""
            if saved_any:
                return last_path
            return self._download_via_ytdlp(
                item, username, shortcode, index, total_items
            )

        # 2. Single Post / Reel / Image / Story -> Direct Stream
        direct_url = str(
            item.get("download_url")
            or item.get("media_url")
            or item.get("video_url")
            or ""
        )

        # Determine media container extension with explicit boolean precedence
        if "is_video" in item:
            is_video = bool(item["is_video"])
        elif bool(item.get("video_url")):
            is_video = True
        else:
            media_type = str(item.get("type") or item.get("media_type") or "").upper()
            if "VIDEO" in media_type or "REEL" in media_type:
                is_video = True
            elif "IMAGE" in media_type or "PHOTO" in media_type:
                is_video = False
            else:
                # URL heuristic fallback
                clean_direct = direct_url.lower().split("?")[0]
                is_image_url = (
                    any(
                        clean_direct.endswith(x)
                        for x in (".jpg", ".jpeg", ".png", ".webp")
                    )
                    or "dst-jpg" in direct_url.lower()
                )
                is_video = not is_image_url

        ext = (
            "mp3"
            if self.quality_preset == "audio_only"
            else ("mp4" if is_video else "jpg")
        )
        target_path = self._build_filepath(
            item_or_username=username,
            shortcode=shortcode,
            ext=ext,
        )

        is_direct_cdn = bool(
            self.quality_preset != "audio_only"
            and direct_url
            and direct_url.startswith("http")
            and not any(
                x in direct_url
                for x in ("/p/", "/reel/", "/reels/", "/tv/", "/stories/")
            )
        )

        if is_direct_cdn:
            try:
                stream_res = self._download_direct_stream(
                    direct_url, target_path, index, total_items
                )
                if (
                    stream_res
                    and os.path.isfile(stream_res)
                    and os.path.getsize(stream_res) > 0
                ):
                    return stream_res
            except Exception as stream_err:
                if self._is_cancelled:
                    return ""
                logger.warning(
                    "Direct stream exception for %s_%s: %s",
                    username,
                    shortcode,
                    stream_err,
                )

        # 3. yt-dlp Scrape Fallback
        return self._download_via_ytdlp(item, username, shortcode, index, total_items)

    @override
    def run(self) -> None:
        total_items = len(self.items)
        if total_items == 0:
            self.progress.emit(100)
            self.finished.emit(0)
            return

        success_count = 0
        logger.info(
            "Starting sequential download queue in chronological order (%d items)",
            total_items,
        )

        try:
            for index, item in enumerate(self.items):
                if self._is_cancelled:
                    logger.warning("Download queue cancelled by user.")
                    break

                item_id = str(
                    item.get("id") or item.get("shortcode") or item.get("url") or index
                )
                self.item_started.emit(item_id)

                try:
                    saved_path = self._process_single_item(item, index, total_items)
                    if self._is_cancelled:
                        self.item_finished.emit(item_id, False)
                        break

                    is_valid = bool(
                        saved_path
                        and os.path.isfile(saved_path)
                        and os.path.getsize(saved_path) > 0
                    )

                    if is_valid:
                        success_count += 1
                        self.item_finished.emit(item_id, True)
                    else:
                        self.item_finished.emit(item_id, False)
                except Exception as exc:
                    if self._is_cancelled:
                        self.item_finished.emit(item_id, False)
                        break
                    logger.error(
                        "Error downloading item %s: %s", item_id, exc, exc_info=True
                    )
                    self.item_finished.emit(item_id, False)

                curr_prog = int(((index + 1) / total_items) * 100)
                self.progress.emit(curr_prog)
        finally:
            try:
                self.session.close()
            except Exception:
                pass

        self.progress.emit(100)
        self.finished.emit(success_count)
