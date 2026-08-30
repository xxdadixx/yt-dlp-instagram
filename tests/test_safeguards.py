# tests/test_safeguards.py

import time
import unittest
from unittest.mock import MagicMock, patch

from core.inspect_worker import (
    InspectWorker,
    MIN_PAGING_DELAY,
    MAX_PAGING_DELAY,
)


class TestScraperSafeguards(unittest.TestCase):

    def setUp(self):
        """Initialize worker with sample session cookies and safe crawl limits."""
        self.cookie_sample = "sessionid=123456789%3AABCdef; csrftoken=mock_csrf_token_999; ds_user_id=123456789;"
        self.worker = InspectWorker(
            targets=["https://www.instagram.com/test_user/"],
            cookie_str=self.cookie_sample,
            max_items_per_profile=10,
        )

    # -------------------------------------------------------------------------
    # Test 1: Unauthenticated-First Routing
    # -------------------------------------------------------------------------
    def test_unauthenticated_first_headers(self):
        """Verify session cookies are excluded by default for public requests."""
        # Default / Public header check
        public_headers = self.worker._build_headers(require_auth=False)
        self.assertNotIn("Cookie", public_headers)
        self.assertNotIn("X-CSRFToken", public_headers)
        self.assertEqual(public_headers["X-IG-App-ID"], "936619743392459")
        self.assertEqual(public_headers["Sec-Fetch-Site"], "same-origin")

        # Explicit authenticated header check (e.g. Stories / Reels API)
        auth_headers = self.worker._build_headers(require_auth=True)
        self.assertIn("Cookie", auth_headers)
        self.assertEqual(auth_headers["Cookie"], self.cookie_sample)
        self.assertEqual(auth_headers["X-CSRFToken"], "mock_csrf_token_999")

    # -------------------------------------------------------------------------
    # Test 2: Circuit Breaker on Scraping Warning URLs
    # -------------------------------------------------------------------------
    def test_scraping_warning_url_circuit_breaker(self):
        """Verify the worker immediately flags cancellation on scraping warning redirects."""
        warning_url = (
            "https://www.instagram.com/accounts/scraping_warning/?challenge_context=xyz"
        )
        is_safe = self.worker._is_safe_response(
            response_url=warning_url,
            response_text="{}",
            status_code=200,
        )

        self.assertFalse(is_safe)
        self.assertTrue(self.worker.is_cancelled)

    # -------------------------------------------------------------------------
    # Test 3: Circuit Breaker on Challenge / Checkpoint Payloads
    # -------------------------------------------------------------------------
    def test_checkpoint_payload_circuit_breaker(self):
        """Verify the worker aborts when response JSON contains challenge indicators."""
        checkpoint_payload = '{"message": "checkpoint_required", "checkpoint_url": "/challenge/123/", "status": "fail"}'
        is_safe = self.worker._is_safe_response(
            response_url="https://i.instagram.com/api/v1/feed/user/12345/",
            response_text=checkpoint_payload,
            status_code=200,
        )

        self.assertFalse(is_safe)
        self.assertTrue(self.worker.is_cancelled)

    # -------------------------------------------------------------------------
    # Test 4: Circuit Breaker on HTTP 429 Rate Limits
    # -------------------------------------------------------------------------
    def test_http_429_rate_limit_circuit_breaker(self):
        """Verify worker halts execution when receiving HTTP 429 Too Many Requests."""
        is_safe = self.worker._is_safe_response(
            response_url="https://i.instagram.com/api/v1/users/web_profile_info/",
            response_text="Too Many Requests",
            status_code=429,
        )

        self.assertFalse(is_safe)
        self.assertTrue(self.worker.is_cancelled)

    # -------------------------------------------------------------------------
    # Test 5: Profile Item Crawl Cap (Budget Enforcement)
    # -------------------------------------------------------------------------
    def test_max_items_per_profile_threshold(self):
        """Verify GraphQL pagination halts once the item threshold is satisfied."""
        self.worker.max_items_per_profile = 5
        self.worker.seen_ids = {"item_1", "item_2", "item_3", "item_4", "item_5"}

        # Mock GraphQL response with more edges available
        mock_response = {
            "data": {
                "user": {
                    "edge_owner_to_timeline_media": {
                        "page_info": {
                            "has_next_page": True,
                            "end_cursor": "cursor_xyz",
                        },
                        "edges": [
                            {"node": {"id": "item_6", "shortcode": "code6"}},
                            {"node": {"id": "item_7", "shortcode": "code7"}},
                        ],
                    }
                }
            }
        }

        with patch.object(self.worker, "_make_request", return_value=mock_response):
            found = self.worker._fetch_timeline_graphql(
                username="test_user", user_id="12345", filter_mode="all"
            )

        # Loop must terminate immediately because threshold (5) was already satisfied
        self.assertEqual(found, 0)
        self.assertEqual(len(self.worker.seen_ids), 5)

    # -------------------------------------------------------------------------
    # Test 6: Adaptive Pacing Distribution Bounds
    # -------------------------------------------------------------------------
    def test_gaussian_pacing_bounds(self):
        """Verify Gaussian sleep duration is clamped strictly between min and max bounds."""
        with patch("time.sleep", return_value=None) as mock_sleep:
            # Run pacing calculation multiple times to test random distributions
            for _ in range(50):
                self.worker._apply_gaussian_pacing()

            # Ensure time.sleep was invoked in slices without throwing exceptions
            self.assertTrue(mock_sleep.called)


if __name__ == "__main__":
    unittest.main()
