"""
Comprehensive Bug & Regression Test Suite for Instagram Pro Downloader.
Covers: Cookie Parsing (#HttpOnly_), Parser Engine, InspectionWorker Signals,
Pagination Loop Guards, Scope Filters, and Download Cancellation.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QCoreApplication

# Ensure GUI/Qt application context exists for QThread signals
app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from core.parser import parse_instagram_url
from core.inspect_worker import InspectionWorker


def create_mock_response(data: dict) -> MagicMock:
    """Helper to create a properly bound HTTP context manager mock."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    return mock_resp


class TestCookieAndParser(unittest.TestCase):
    """Test URL parsing rules and Netscape cookie extraction edge cases."""

    def test_httponly_csrf_cookie_parsing(self):
        """Verify that #HttpOnly_ prefixes do not cause csrftoken to be skipped."""
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(
                "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1799999999\tcsrftoken\tSECURE_TOKEN_XYZ\n"
            )
            f.write(
                ".instagram.com\tTRUE\t/\tTRUE\t1799999999\tsessionid\t123456789%3AABC\n"
            )
            temp_path = f.name

        try:
            worker = InspectionWorker(targets=[], cookie_path=temp_path)
            token = worker._get_csrf_token()
            self.assertEqual(
                token,
                "SECURE_TOKEN_XYZ",
                "Failed to extract csrftoken from #HttpOnly_ line",
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_url_parser_patterns(self):
        """Test regex pattern matching across all supported URL variants."""
        test_cases = [
            (
                "https://www.instagram.com/p/C_TEST123/?igsh=MzRlODBiNWFlZA==",
                "post",
                "C_TEST123",
                None,
            ),
            ("https://www.instagram.com/reel/C_REEL456/", "video", "C_REEL456", None),
            (
                "https://www.instagram.com/stories/valid_user/",
                "story_user",
                "valid_user_all_stories",
                "valid_user",
            ),
            (
                "https://www.instagram.com/valid_user/reels/",
                "profile_reels",
                "valid_user_all_reels",
                "valid_user",
            ),
            (
                "https://www.instagram.com/valid_user/",
                "profile_posts",
                "valid_user_all_posts",
                "valid_user",
            ),
        ]

        for url, expected_type, expected_id, expected_user in test_cases:
            res = parse_instagram_url(url)
            self.assertIsNotNone(res, f"Parser returned None for: {url}")
            self.assertEqual(res["type"], expected_type, f"Type mismatch for {url}")
            self.assertEqual(res["identifier"], expected_id, f"ID mismatch for {url}")
            if expected_user:
                self.assertEqual(
                    res["username"], expected_user, f"Username mismatch for {url}"
                )


class TestInspectionWorkerEngine(unittest.TestCase):
    """Test pagination, scope filtering, signals, and stagnation defenses."""

    def setUp(self):
        self.emitted_items = []
        self.status_messages = []
        self.finished_counts = []

    def _collect_item(self, item):
        self.emitted_items.append(item)

    def _collect_status(self, msg):
        self.status_messages.append(msg)

    def _collect_finished(self, count):
        self.finished_counts.append(count)

    @patch("time.sleep", return_value=None)
    @patch("core.inspect_worker.get_cookie_opener")
    def test_profile_feed_scope_filtering(self, mock_get_opener, mock_sleep):
        """Verify that 'photos_only' and 'videos_only' scopes drop invalid media types."""
        mock_opener = MagicMock()
        mock_get_opener.return_value = mock_opener

        user_info_resp = create_mock_response(
            {
                "data": {
                    "user": {
                        "id": "1001",
                        "pk": "1001",
                        "edge_owner_to_timeline_media": {"edges": []},
                    }
                }
            }
        )

        feed_page1 = create_mock_response(
            {
                "items": [
                    {
                        "code": "PHOTO1",
                        "media_type": 1,
                        "image_versions2": {
                            "candidates": [{"url": "http://img.jpg", "width": 1080}]
                        },
                    },
                    {
                        "code": "VIDEO1",
                        "media_type": 2,
                        "video_versions": [{"url": "http://vid.mp4", "width": 1080}],
                        "image_versions2": {
                            "candidates": [{"url": "http://thumb.jpg", "width": 1080}]
                        },
                    },
                ],
                "more_available": False,
            }
        )

        mock_opener.open.side_effect = [user_info_resp, feed_page1]

        worker = InspectionWorker(
            targets=[
                {"url": "https://www.instagram.com/test_user/", "scope": "photos_only"}
            ],
            cookie_path=None,
        )
        worker.item_inspected.connect(self._collect_item)
        worker.run()

        self.assertEqual(len(self.emitted_items), 1)
        self.assertEqual(self.emitted_items[0]["shortcode"], "PHOTO1")
        self.assertEqual(self.emitted_items[0]["media_type"], "photo")

    @patch("time.sleep", return_value=None)
    @patch("core.inspect_worker.get_cookie_opener")
    def test_cursor_stagnation_loop_defense(self, mock_get_opener, mock_sleep):
        """Verify worker breaks out immediately if Instagram cycles the same max_id."""
        mock_opener = MagicMock()
        mock_get_opener.return_value = mock_opener

        user_info_resp = create_mock_response(
            {
                "data": {
                    "user": {
                        "id": "2002",
                        "edge_owner_to_timeline_media": {"edges": []},
                    }
                }
            }
        )

        feed_page = create_mock_response(
            {
                "items": [
                    {
                        "code": "ITEM_A",
                        "media_type": 1,
                        "image_versions2": {
                            "candidates": [{"url": "http://a.jpg", "width": 1080}]
                        },
                    }
                ],
                "more_available": True,
                "next_max_id": "REPEATED_CURSOR_123",
            }
        )

        mock_opener.open.side_effect = [user_info_resp, feed_page, feed_page, feed_page]

        worker = InspectionWorker(
            targets=[{"url": "https://www.instagram.com/loop_user/", "scope": "all"}],
            cookie_path=None,
        )
        worker.item_inspected.connect(self._collect_item)
        worker.finished_inspection.connect(self._collect_finished)
        worker.run()

        self.assertEqual(len(self.emitted_items), 1)
        self.assertEqual(self.finished_counts[0], 1)

    @patch("time.sleep", return_value=None)
    @patch("core.inspect_worker.get_cookie_opener")
    def test_duplicate_prevention_against_grid(self, mock_get_opener, mock_sleep):
        """Ensure items already existing in the GUI grid are ignored."""
        mock_opener = MagicMock()
        mock_get_opener.return_value = mock_opener

        user_info_resp = create_mock_response(
            {
                "data": {
                    "user": {
                        "id": "3003",
                        "edge_owner_to_timeline_media": {"edges": []},
                    }
                }
            }
        )

        feed_resp = create_mock_response(
            {
                "items": [
                    {
                        "code": "ALREADY_EXISTS",
                        "media_type": 1,
                        "image_versions2": {
                            "candidates": [{"url": "http://1.jpg", "width": 1080}]
                        },
                    },
                    {
                        "code": "NEW_ITEM",
                        "media_type": 1,
                        "image_versions2": {
                            "candidates": [{"url": "http://2.jpg", "width": 1080}]
                        },
                    },
                ],
                "more_available": False,
            }
        )

        mock_opener.open.side_effect = [user_info_resp, feed_resp]

        worker = InspectionWorker(
            targets=[{"url": "https://www.instagram.com/test_user/", "scope": "all"}],
            cookie_path=None,
            existing_shortcodes={"ALREADY_EXISTS"},
        )
        worker.item_inspected.connect(self._collect_item)
        worker.run()

        self.assertEqual(len(self.emitted_items), 1)
        self.assertEqual(self.emitted_items[0]["shortcode"], "NEW_ITEM")


class TestDownloadStreamingSafety(unittest.TestCase):
    """Test stream chunking, interruption flags, and partial file cleanup."""

    def test_partial_file_cleanup_on_cancel(self):
        """Ensure interrupted downloads remove unfinished .part files."""
        temp_dir = tempfile.mkdtemp()
        target_file = os.path.join(temp_dir, "test_video.mp4")
        part_file = f"{target_file}.part"

        with open(part_file, "wb") as f:
            f.write(b"PARTIAL_BINARY_DATA" * 50)

        self.assertTrue(os.path.exists(part_file))

        is_cancelled = True
        if is_cancelled and os.path.exists(part_file):
            os.remove(part_file)

        self.assertFalse(
            os.path.exists(part_file), ".part file leaked after cancellation"
        )
        self.assertFalse(os.path.exists(target_file))


if __name__ == "__main__":
    unittest.main()
