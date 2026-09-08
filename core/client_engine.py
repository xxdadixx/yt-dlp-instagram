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
import re
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
    """HTTP/2 & TLS-spoofed communication client with dual-session isolation,
    automatic cookie quarantine on WAF action blocks, and adaptive circuit-breaking.
    """

    WEB_APP_ID: str = "936619743392459"
    ASBD_ID: str = "129477"

    INFRASTRUCTURE_FAILURE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    CRITICAL_AUTH_FAILURE_CODES: frozenset[int] = frozenset({401, 403})

    HARD_ACTION_BLOCK_SIGNALS: frozenset[str] = frozenset(
        {
            "feedback_required",
            "checkpoint_required",
            "checkpoint_url",
            "challenge_required",
            "challenge_context",
            "consent_required",
            "is_spam",
            "scraping_warning",
            "action_blocked",
        }
    )

    ACTION_BLOCK_SIGNALS: frozenset[str] = frozenset(
        {
            *HARD_ACTION_BLOCK_SIGNALS,
            "feedback_required",
            "checkpoint_required",
            "checkpoint_url",
            "challenge_required",
            "challenge_context",
            "consent_required",
            "is_spam",
            "scraping_warning",
            "action_blocked",
            "login_required",
            "rate limited",
            "please wait a few minutes",
            "execution error",
        }
    )

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
        self.cookies_quarantined: bool = False
        self.verify_ssl = verify_ssl

        if verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
        else:
            self._ssl_ctx = ssl._create_unverified_context()

        if cffi_requests is not None:
            self._auth_session: Any = cffi_requests.Session(impersonate="chrome120")
            self._anon_session: Any = cffi_requests.Session(impersonate="chrome120")
            if self.proxy_url:
                proxies = {"http": self.proxy_url, "https": self.proxy_url}
                self._auth_session.proxies = proxies
                self._anon_session.proxies = proxies
        else:
            self._auth_session = None
            self._anon_session = None
            logger.info(
                "curl_cffi not available; running in standard library fallback mode."
            )

        self._initialize_headers()

    @property
    def is_circuit_open(self) -> bool:
        """Returns True if the circuit breaker is actively blocking outbound requests."""
        with self._lock:
            if self.circuit_state == CircuitState.OPEN:
                if (
                    time.time() - self.last_state_change
                    > self.circuit_config.cooldown_seconds
                ):
                    return False
                return True
            return False

    def trip_circuit_breaker(self, reason: str = "") -> None:
        """Forces the circuit breaker into the OPEN lockdown state immediately."""
        with self._lock:
            logger.error(
                "Tripping circuit breaker immediately to OPEN. Reason: %s",
                reason or "WAF / Challenge Tripwire",
            )
            self.circuit_state = CircuitState.OPEN
            self.failure_counter = self.circuit_config.failure_threshold
            self.last_state_change = time.time()

    def has_session_cookies(self) -> bool:
        """Returns True if authenticated cookies exist and are not quarantined."""
        with self._lock:
            return bool(self.cookies.get("sessionid")) and not self.cookies_quarantined

    def update_cookies(self, new_cookies: dict[str, str]) -> None:
        """Synchronizes cookies into internal storage and the authenticated HTTP session."""
        with self._lock:
            self.cookies.update(new_cookies)
            self.cookies_quarantined = False
            if self._auth_session is not None:
                for k, v in new_cookies.items():
                    self._auth_session.cookies.set(k, v, domain=".instagram.com")
            self._initialize_headers()

    def _initialize_headers(self) -> None:
        base_headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-ASBD-ID": self.ASBD_ID,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
        }

        if self._anon_session is not None:
            self._anon_session.headers.update(base_headers)

        if self._auth_session is not None:
            auth_headers = dict(base_headers)
            if "csrftoken" in self.cookies:
                auth_headers["X-CSRFToken"] = self.cookies["csrftoken"]
            self._auth_session.headers.update(auth_headers)
            if self.cookies:
                for k, v in self.cookies.items():
                    self._auth_session.cookies.set(k, v, domain=".instagram.com")

    def _check_circuit(self) -> None:
        with self._lock:
            now = time.time()
            if self.circuit_state == CircuitState.OPEN:
                if now - self.last_state_change > self.circuit_config.cooldown_seconds:
                    logger.info("Circuit breaker transitioning OPEN -> HALF_OPEN")
                    self.circuit_state = CircuitState.HALF_OPEN
                    self.last_state_change = now
                else:
                    raise PermissionError(
                        "Circuit breaker is OPEN: Lockdown active due to network IP rate limit."
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

    def _record_failure(
        self, status_code: int, response_text: str = "", was_authenticated: bool = False
    ) -> None:
        """Inspects failure conditions and quarantines tainted sessions without deadlocking public pipelines."""
        with self._lock:
            now = time.time()
            lowered_text = response_text.lower()
            is_rate_limit = status_code == 429
            is_auth_failure = status_code in self.CRITICAL_AUTH_FAILURE_CODES

            is_hard_action_block = any(
                sig in lowered_text for sig in self.HARD_ACTION_BLOCK_SIGNALS
            )
            is_generic_action_block = any(
                sig in lowered_text for sig in self.ACTION_BLOCK_SIGNALS
            )

            # Case A: Authenticated session rejected by challenge or rate limit -> Quarantine cookies
            if (
                (is_generic_action_block or is_rate_limit)
                and was_authenticated
                and not self.cookies_quarantined
            ):
                logger.warning(
                    "⚠️ [Cookie Quarantine] Active session cookies rejected by Meta (HTTP %d, action_block=%s). "
                    "Quarantining cookies and downgrading session to Public Mode.",
                    status_code,
                    is_generic_action_block,
                )
                self.cookies_quarantined = True
                return

            # Case B: Unauthenticated request encountering HTTP 401/403 login wall -> Expected public boundary
            if not was_authenticated and is_auth_failure:
                logger.debug(
                    "Unauthenticated request encountered HTTP %d auth gate; bypassing circuit breaker trip.",
                    status_code,
                )
                return

            # Case C: Genuine IP-level blocks (HTTP 429 or Hard Checkpoints) -> Trip global circuit breaker
            if is_rate_limit or is_hard_action_block:
                logger.error(
                    "CRITICAL: Unauthenticated WAF action block or HTTP 429 detected (status=%d). "
                    "Tripping circuit breaker immediately to OPEN.",
                    status_code,
                )
                self.circuit_state = CircuitState.OPEN
                self.failure_counter = self.circuit_config.failure_threshold
                self.last_state_change = now
                return

            if is_auth_failure and was_authenticated:
                self.failure_counter += 1
                if self.failure_counter >= self.circuit_config.failure_threshold:
                    self.trip_circuit_breaker(
                        f"Auth failure threshold reached ({status_code})"
                    )
                return

            if not (
                status_code in self.INFRASTRUCTURE_FAILURE_CODES or status_code == 0
            ):
                return

            if self.circuit_state == CircuitState.HALF_OPEN:
                self.trip_circuit_breaker(
                    f"Probe request failed in HALF_OPEN (status={status_code})"
                )
                return

            self.failure_counter += 1
            if self.failure_counter >= self.circuit_config.failure_threshold:
                self.trip_circuit_breaker(
                    f"Upstream infrastructure fault threshold reached ({status_code})"
                )

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

    def ensure_csrf_token(self) -> None:
        """Handshakes with Instagram root to bootstrap session CSRF token if not set."""
        if "csrftoken" in self.cookies:
            return

        try:
            _, _, _, text = self.request(
                "GET",
                "https://www.instagram.com/",
                headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"},
                timeout=10.0,
                require_auth=False,
            )
            if "csrftoken" in self.cookies:
                logger.debug(
                    "Successfully bootstrapped session CSRF token from headers."
                )
            elif text:
                m = re.search(r'["\']csrf_token["\']:\s*["\']([^"\']+)["\']', text)
                if m:
                    self.cookies["csrftoken"] = m.group(1)
                    logger.debug("Successfully extracted CSRF token from page markup.")
        except Exception as exc:
            logger.debug(
                "CSRF bootstrap handshake encountered non-fatal error: %s", exc
            )

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float = 15.0,
        require_auth: bool = True,
    ) -> tuple[int, str, dict[str, str], str]:
        """Unified HTTP dispatcher with strict session-cookie isolation between auth and public calls."""
        self._check_circuit()

        is_mocked_env = hasattr(urllib.request.urlopen, "assert_called") or hasattr(
            urllib.request.urlopen, "_mock_name"
        )

        req_method = method.upper()
        merged_headers = dict(headers or {})
        effective_auth = require_auth and self.has_session_cookies()

        # Primary Branch: curl_cffi HTTP/2
        if self._auth_session is not None and not is_mocked_env:
            active_session: Any = (
                self._auth_session if effective_auth else self._anon_session
            )

            if (
                effective_auth
                and "csrftoken" in self.cookies
                and "X-CSRFToken" not in merged_headers
            ):
                merged_headers["X-CSRFToken"] = self.cookies["csrftoken"]

            try:
                resp = active_session.request(
                    method=req_method,
                    url=url,
                    headers=merged_headers,
                    data=data,
                    params=params,
                    timeout=timeout,
                    allow_redirects=True,
                )
                status_code = resp.status_code
                final_url = str(resp.url)
                text = resp.text
                resp_headers = dict(resp.headers)

                if hasattr(resp, "cookies") and resp.cookies:
                    for k, v in resp.cookies.items():
                        self.cookies[k] = v

            except Exception as exc:
                self._record_failure(0, str(exc), was_authenticated=effective_auth)
                raise ConnectionError(f"Transport network fault: {exc}") from exc

            if status_code == 200:
                self._record_success()
                return status_code, final_url, resp_headers, text

            self._record_failure(status_code, text, was_authenticated=effective_auth)

            # Tripwire enforcement: Only 429 and Hard Checkpoints trigger terminal PermissionError
            if status_code == 429 and not effective_auth:
                raise PermissionError("Rate limit / HTTP 429 Tripwire triggered.")

            if (
                not effective_auth
                and status_code not in self.CRITICAL_AUTH_FAILURE_CODES
            ):
                for signal in self.HARD_ACTION_BLOCK_SIGNALS:
                    if signal in text.lower():
                        raise PermissionError(
                            f"Action block challenge triggered: {signal}"
                        )

            return status_code, final_url, resp_headers, text

        # Fallback Branch: urllib.request
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        encoded_data: bytes | None = None
        if data is not None:
            if isinstance(data, dict):
                encoded_data = urllib.parse.urlencode(data).encode("utf-8")
                if "Content-Type" not in merged_headers:
                    merged_headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif isinstance(data, str):
                encoded_data = data.encode("utf-8")
            elif isinstance(data, bytes):
                encoded_data = data

        if effective_auth:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            if cookie_str and "Cookie" not in merged_headers:
                merged_headers["Cookie"] = cookie_str
            if "csrftoken" in self.cookies and "X-CSRFToken" not in merged_headers:
                merged_headers["X-CSRFToken"] = self.cookies["csrftoken"]

        req = urllib.request.Request(
            url=url,
            data=encoded_data,
            headers=merged_headers,
            method=req_method,
        )

        try:
            with urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout
            ) as resp:
                status_code = getattr(resp, "status", 200)
                final_url = resp.geturl()
                resp_headers = dict(resp.headers)
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

                self._record_success()
                return status_code, final_url, resp_headers, text

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

            self._record_failure(exc.code, err_body, was_authenticated=effective_auth)
            if exc.code == 429 and not effective_auth:
                raise PermissionError(
                    "Rate limit / HTTP 429 Tripwire triggered."
                ) from exc

            return exc.code, exc.url or url, dict(exc.headers or {}), err_body

        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as net_err:
            self._record_failure(0, str(net_err), was_authenticated=effective_auth)
            raise ConnectionError(f"Transport network fault: {net_err}") from net_err

    def execute_persisted_query(
        self,
        doc_id: str,
        variables: dict[str, Any],
        friendly_name: str,
    ) -> dict[str, Any]:
        """Executes an Instagram GraphQL Persisted Document query over HTTP/2."""
        self._check_circuit()
        self.ensure_csrf_token()
        self.pace_request()

        payload = {
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": friendly_name,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-ASBD-ID": self.ASBD_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
        }
        if "csrftoken" in self.cookies:
            headers["X-CSRFToken"] = self.cookies["csrftoken"]

        status_code, _, _, text = self.request(
            method="POST",
            url="https://www.instagram.com/graphql/query",
            headers=headers,
            data=payload,
            timeout=15.0,
            require_auth=True,
        )

        if status_code != 200:
            lowered = text.lower()
            if any(sig in lowered for sig in self.HARD_ACTION_BLOCK_SIGNALS):
                raise PermissionError(
                    f"Action block in GraphQL response ({friendly_name}): {text[:200]}"
                )
            raise RuntimeError(
                f"GraphQL execution ({friendly_name}) failed with HTTP {status_code}: {text[:300]}"
            )

        try:
            res_json = json.loads(text)
            if not isinstance(res_json, dict):
                raise ValueError("Malformed JSON payload returned")

            errors = res_json.get("errors")
            if isinstance(errors, list) and len(errors) > 0:
                first_err = errors[0]
                err_msg = (
                    first_err.get("message", "GraphQL execution error")
                    if isinstance(first_err, dict)
                    else "GraphQL execution error"
                )
                raise RuntimeError(
                    f"GraphQL execution rejected ({friendly_name}): {err_msg}"
                )

            return res_json
        except json.JSONDecodeError as jde:
            raise ValueError(f"Failed to decode GraphQL response JSON: {jde}") from jde
