# core/client_engine.py
# (Context: Replace lines 1-60 and execute_persisted_query in ResilientSession)

from __future__ import annotations

import gzip
import json
import logging
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
import importlib
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
    """HTTP/2 & TLS-spoofed communication client with adaptive circuit-breaking,

    dynamic Gaussian-jitter request pacing, and standard library fallback.
    """

    WEB_APP_ID = "936619743392459"
    ASBD_ID = "129477"

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        proxy_url: str | None = None,
        circuit_config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.circuit_config = circuit_config or CircuitBreakerConfig()
        self.circuit_state = CircuitState.CLOSED
        self.failure_counter = 0
        self.last_state_change = time.time()
        self.proxy_url = proxy_url
        self.cookies: dict[str, str] = cookies or {}
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
        if self.circuit_state == CircuitState.HALF_OPEN:
            logger.info(
                "Probe succeeded. Circuit breaker transitioning HALF_OPEN -> CLOSED"
            )
            self.circuit_state = CircuitState.CLOSED
            self.failure_counter = 0
            self.last_state_change = time.time()

    def _record_failure(self, status_code: int) -> None:
        self.failure_counter += 1
        logger.warning(
            "Request failed with status %d (Failures: %d)",
            status_code,
            self.failure_counter,
        )
        if self.failure_counter >= self.circuit_config.failure_threshold:
            logger.error("Failure threshold reached. Tripping circuit breaker to OPEN.")
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

        # --- Primary Branch: curl_cffi (JA4 + HTTP/2 Impersonation) ---
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
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
            self._record_failure(exc.code)
            if exc.code == 429:
                raise PermissionError(
                    "Rate limit / HTTP 429 Tripwire triggered."
                ) from exc
            raise RuntimeError(
                f"GraphQL execution failed with HTTP {exc.code}"
            ) from exc
        except Exception as exc:
            self._record_failure(0)
            raise ConnectionError(f"Fallback request network fault: {exc}") from exc
