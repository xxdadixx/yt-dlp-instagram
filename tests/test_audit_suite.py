"""
tests/test_audit_suite.py - Deep test suite covering all 4 core audit areas:
1. Thread Safety & Memory Management
2. Network Resilience & Fallback Tiers
3. URL Parsing & Input Normalization
4. UI Responsiveness & Batch Rendering
"""

import io
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, os.path.abspath("."))

from config.constants import (
    MEDIA_TYPE_CAROUSEL,
    MEDIA_TYPE_PHOTO,
    MEDIA_TYPE_VIDEO,
    POST_REEL_REGEX,
    REELS_TAB_REGEX,
    RESERVED_USERNAMES,
)
from core.download_worker import DownloadWorker, sanitize_filename
from core.inspect_worker import InspectWorker
from core.parser import (
    extract_instagram_urls,
    id_to_shortcode,
    is_standalone_video,
    normalize_url,
    parse_instagram_url,
    shortcode_to_id,
)
from gui.main_window import MainWindow
from gui.widgets.media_card import MediaCard
from gui.widgets.thumbnail_loader import ThumbnailCache, ThumbnailLoader
from gui.widgets.url_chip_input import URLChipInput


class TestThreadSafetyAndMemory(unittest.TestCase):
  """Area 1: Thread Safety & Memory Management Tests"""

  def setUp(self):
    ThumbnailCache.clear()

  def test_thumbnail_cache_thread_safety(self):
    errors = []

    def worker(tid):
      try:
        for i in range(50):
          url = f"https://example.com/thumb_{tid}_{i}.jpg"
          data = f"data_{tid}_{i}".encode("utf-8")
          ThumbnailCache.set(url, data)
          retrieved = ThumbnailCache.get(url)
          if retrieved != data:
            errors.append(f"Mismatch at {url}")
      except Exception as ex:
        errors.append(str(ex))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
      t.start()
    for t in threads:
      t.join()

    self.assertEqual(len(errors), 0, f"Thread safety errors: {errors}")

  def test_media_card_lifecycle_and_teardown(self):
    item_data = {
        "id": "987654",
        "shortcode": "TestShortcode123",
        "title": "Test Title",
        "username": "creator",
        "url": "https://www.instagram.com/reel/TestShortcode123/",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "media_type": "reel",
        "selected": True,
        "status": "ready",
    }
    card = MediaCard(item_data)
    self.assertIsNotNone(card.thumb_loader)
    self.assertFalse(card._is_cleaned_up)

    card.cleanup()
    self.assertTrue(card._is_cleaned_up)
    self.assertIsNone(card.thumb_loader)


class TestNetworkResilienceAndFallbacks(unittest.TestCase):
  """Area 2: Network Resilience & Fallback Tiers Tests"""

  def test_http_429_rate_limit_resilience(self):
    worker = InspectWorker(
        targets=["https://www.instagram.com/kanyxxon/reels/"]
    )
    status_msgs = []
    worker.status_message.connect(lambda msg: status_msgs.append(msg))

    fp = io.BytesIO(b'{"message": "rate limited", "status": "fail"}')
    http_429 = urllib.error.HTTPError(
        url="https://i.instagram.com/api/v1/clips/user/",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=fp,
    )

    with patch("urllib.request.urlopen", side_effect=http_429):
      res = worker._make_request("https://i.instagram.com/api/v1/clips/user/")
      self.assertIsNone(res)

    self.assertTrue(any("429" in msg for msg in status_msgs))

  def test_single_post_embed_json_and_img_index_extraction(self):
    worker = InspectWorker(targets=[])
    embed_html_with_json = """
        <html><body><script>
        window.__additionalDataLoaded('/p/DbDK2ArgQXF/', {
          "graphql": {
            "shortcode_media": {
              "id": "3946045390553417157",
              "shortcode": "DbDK2ArgQXF",
              "owner": {"username": "traveler"},
              "edge_sidecar_to_children": {
                "edges": [
                  {"node": {"id": "101", "display_url": "https://cdn.example.com/slide1.jpg", "is_video": false}},
                  {"node": {"id": "102", "display_url": "https://cdn.example.com/slide2.jpg", "video_url": "https://cdn.example.com/slide2.mp4", "is_video": true}}
                ]
              },
              "edge_media_to_caption": {"edges": [{"node": {"text": "My carousel adventure"}}]}
            }
          }
        });
        </script></body></html>
        """
    card1 = worker._extract_from_embed_html(
        embed_html_with_json,
        "DbDK2ArgQXF",
        raw_target="https://www.instagram.com/p/DbDK2ArgQXF/?img_index=1",
    )
    self.assertIsNotNone(card1)
    self.assertEqual(
        card1["thumbnail_url"], "https://cdn.example.com/slide1.jpg"
    )
    self.assertEqual(card1["username"], "traveler")
    self.assertIn("Slide 1", card1["title"])


class TestURLParsingAndNormalization(unittest.TestCase):
  """Area 3: URL Parsing & Input Normalization Tests"""

  def test_tracking_params_and_punctuation_stripping(self):
    raw_urls = [
        (
            (
                "https://www.instagram.com/reel/C_abc123/?igsh=MWF5ZXF4czFmdjB4bw=="
            ),
            "reel",
            "C_abc123",
        ),
        ("https://www.instagram.com/share/reel/C_abc123/", "reel", "C_abc123"),
        ("https://www.instagram.com/share/p/D_xyz456/", "post", "D_xyz456"),
        ("https://instagram.com/p/D_xyz456/?img_index=2", "carousel", "D_xyz456"),
        (
            "https://www.instagram.com/stories/creator/99887766/?igsh=123",
            "story",
            None,
        ),
        ("@creator_handle", "profile", None),
    ]

    for raw, exp_type, exp_code in raw_urls:
      parsed = parse_instagram_url(raw)
      self.assertTrue(parsed["valid"], f"Failed for {raw}")
      self.assertEqual(parsed["type"], exp_type, f"Type failed for {raw}")
      if exp_code:
        self.assertEqual(
            parsed["shortcode"], exp_code, f"Code failed for {raw}"
        )


class TestUIResponsivenessAndBatchRendering(unittest.TestCase):
  """Area 4: UI Responsiveness & Batch Rendering Tests"""

  def test_batch_card_addition_and_selection(self):
    win = MainWindow()
    for i in range(100):
      item_data = {
          "id": str(1000 + i),
          "shortcode": f"Reel_{i:03d}",
          "title": f"Reel Caption {i}",
          "username": "creator",
          "url": f"https://www.instagram.com/reel/Reel_{i:03d}/",
          "thumbnail_url": "",
          "duration": 15.0,
          "view_count": 1000 * i,
          "like_count": 100 * i,
          "media_type": "reel",
          "selected": True,
          "status": "ready",
      }
      win.add_card(item_data)

    self.assertEqual(len(win.cards), 100)
    self.assertIn("100 / 100", win.lbl_selected_count.text())

    win.clear_media_grid()
    self.assertEqual(len(win.cards), 0)


if __name__ == "__main__":
  unittest.main()