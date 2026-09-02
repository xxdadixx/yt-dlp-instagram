"""
core/client_engine.py - HTTP/2 & TLS-spoofed communication client with adaptive circuit-breaking,
dynamic Gaussian-jitter request pacing, and thread-safe fallback execution.
"""

from __future__ import annotations

import gzip
import importlib
import json
import logging
import random
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    cffi_requests: Any = importlib.import_module("curl_cffi.requests")
except Exception:
    cffi_requests = None

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"
    OPEN = "OPEN"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0


class ResilientSession:
    """HTTP/2 & TLS-spoofed communication client with adaptive circuit-breaking

    and thread-safe standard library fallback.
    """

    WEB_APP_ID = "936619743392459"
    ASBD_ID = "129477"

    # Status codes that indicate infrastructure/traffic denial rather than client payload issues
    INFRASTRUCTURE_FAILURE_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        proxy_url: str | None = None,
        circuit_config: CircuitBreakerConfig | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self.circuit_config = circuit_config or CircuitBreakerConfig()
        self.circuit_state = CircuitState.CLOSED
        self.failure_counter = 0
        self.last_state_change = time.time()
        self.proxy_url = proxy_url
        self.cookies: dict[str, str] = dict(cookies or {})

        if verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
        else:
            self._ssl_ctx = ssl._create_unverified_context()

        if cffi_requests is not None:
            self._session = cffi_requests.Session(impersonate="chrome120")
            if self.proxy_url:
                self._session.proxies = {
                    "http": self.proxy_url,
                    "https": self.proxy_url,
                }
        else:
            self._session = None
            logger.info(
                "curl_cffi not available; running in standard library fallback mode."
            )

        self._initialize_headers()

    def _initialize_headers(self) -> None:
        if self._session is not None:
            self._session.headers.update(
                {
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Origin": "https://www.instagram.com",
                    "Referer": "https://www.instagram.com/",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "X-ASBD-ID": self.ASBD_ID,
                    "X-IG-App-ID": self.WEB_APP_ID,
                }
            )
            if self.cookies:
                for k, v in self.cookies.items():
                    self._session.cookies.set(k, v, domain=".instagram.com")
                if "csrftoken" in self.cookies:
                    self._session.headers["X-CSRFToken"] = self.cookies["csrftoken"]

    def _check_circuit(self) -> None:
        with self._lock:
            now = time.time()
            if self.circuit_state == CircuitState.OPEN:
                if now - self.last_state_change > self.circuit_config.cooldown_seconds:
                    logger.info("Circuit breaker transitioning OPEN -> HALF_OPEN")
                    self.circuit_state = CircuitState.HALF_OPEN
                    self.last_state_change = now
                else:
                    raise RuntimeError(
                        "Circuit breaker is OPEN: Rate limit lockdown active."
                    )

    def _record_success(self) -> None:
        with self._lock:
            if self.circuit_state == CircuitState.HALF_OPEN:
                logger.info(
                    "Probe succeeded. Circuit breaker transitioning HALF_OPEN -> CLOSED"
                )
            self.circuit_state = CircuitState.CLOSED
            self.failure_counter = 0
            self.last_state_change = time.time()

    def _record_failure(self, status_code: int) -> None:
        with self._lock:
            # Only infrastructure faults and rate limits increment circuit breaker trips
            if (
                status_code != 0
                and status_code not in self.INFRASTRUCTURE_FAILURE_CODES
            ):
                logger.debug(
                    "Non-infrastructure status code %d ignored by circuit breaker.",
                    status_code,
                )
                return

            self.failure_counter += 1
            logger.warning(
                "Upstream fault registered with status %d (Failures: %d/%d)",
                status_code,
                self.failure_counter,
                self.circuit_config.failure_threshold,
            )
            if self.failure_counter >= self.circuit_config.failure_threshold:
                logger.error(
                    "Failure threshold reached. Tripping circuit breaker to OPEN."
                )
                self.circuit_state = CircuitState.OPEN
                self.last_state_change = time.time()

    def pace_request(
        self,
        mu: float = 2.8,
        sigma: float = 0.5,
        min_t: float = 1.8,
        max_t: float = 5.0,
    ) -> None:
        delay = random.gauss(mu, sigma)
        delay = max(min_t, min(delay, max_t))
        time.sleep(delay)

    def _build_cookie_header(self) -> str:
        with self._lock:
            if not self.cookies:
                return ""
            return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def execute_persisted_query(
        self,
        doc_id: str,
        variables: dict[str, Any],
        friendly_name: str,
    ) -> dict[str, Any]:
        self._check_circuit()
        self.pace_request()

        payload = {
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
        }

        # --- Primary Branch: curl_cffi (HTTP/2 Impersonation) ---
        if self._session is not None:
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-FB-Friendly-Name": friendly_name,
            }
            try:
                response = self._session.post(
                    "https://www.instagram.com/graphql/query",
                    data=payload,
                    headers=headers,
                    timeout=15,
                )
            except Exception as exc:
                self._record_failure(0)
                raise ConnectionError(f"Transport network fault: {exc}") from exc

            if response.status_code == 200:
                self._record_success()
                try:
                    res_json = response.json()
                    if not isinstance(res_json, dict):
                        raise ValueError("Malformed JSON payload returned")
                    return res_json
                except Exception as exc:
                    raise ValueError(f"Failed to decode GraphQL JSON: {exc}") from exc

            self._record_failure(response.status_code)
            if response.status_code == 429:
                raise PermissionError("Rate limit / HTTP 429 Tripwire triggered.")
            if "checkpoint_required" in response.text:
                raise PermissionError("Identity checkpoint challenge required.")

            raise RuntimeError(
                f"GraphQL execution failed with status {response.status_code}"
            )

        # --- Fallback Branch: Standard Library urllib.request ---
        post_data = urllib.parse.urlencode(payload).encode("utf-8")
        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-ASBD-ID": self.ASBD_ID,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-FB-Friendly-Name": friendly_name,
        }

        cookie_str = self._build_cookie_header()
        if cookie_str:
            req_headers["Cookie"] = cookie_str
            if "csrftoken" in self.cookies:
                req_headers["X-CSRFToken"] = self.cookies["csrftoken"]

        req = urllib.request.Request(
            "https://www.instagram.com/graphql/query",
            data=post_data,
            headers=req_headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
                raw_bytes = resp.read()
                content_encoding = resp.headers.get("Content-Encoding", "").lower()

                if "gzip" in content_encoding or (
                    len(raw_bytes) >= 2 and raw_bytes[:2] == b"\x1f\x8b"
                ):
                    raw_bytes = gzip.decompress(raw_bytes)
                elif "deflate" in content_encoding:
                    try:
                        raw_bytes = zlib.decompress(raw_bytes)
                    except Exception:
                        raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)

                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw_bytes.decode(charset, errors="replace").strip()
                res_json = json.loads(text)

                if not isinstance(res_json, dict):
                    raise ValueError("Malformed JSON returned by endpoint")

                self._record_success()
                return res_json

        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                raw_err = exc.read()
                content_encoding = exc.headers.get("Content-Encoding", "").lower()
                if "gzip" in content_encoding or (
                    len(raw_err) >= 2 and raw_err[:2] == b"\x1f\x8b"
                ):
                    raw_err = gzip.decompress(raw_err)
                elif "deflate" in content_encoding:
                    raw_err = zlib.decompress(raw_err)
                err_body = raw_err.decode("utf-8", errors="replace").strip()
            except Exception:
                pass

            self._record_failure(exc.code)
            logger.debug(
                "[%s] HTTP %d Response Body: %s", friendly_name, exc.code, err_body
            )

            if exc.code == 429:
                raise PermissionError(
                    "Rate limit / HTTP 429 Tripwire triggered."
                ) from exc
            raise RuntimeError(
                f"GraphQL execution ({friendly_name}) failed with HTTP {exc.code}: {err_body}"
            ) from exc
