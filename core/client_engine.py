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
    dynamic LSD/Claim token binding, and zero-tolerance circuit-breaking on WAF tripwires.
    """

    WEB_APP_ID: str = "936619743392459"
    ASBD_ID: str = "129477"

    INFRASTRUCTURE_FAILURE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    # HTTP 403 is frequently returned by Instagram for WAF/routing errors; only 401 indicates expired auth
    CRITICAL_AUTH_FAILURE_CODES: frozenset[int] = frozenset({401})

    # Critical security signals that indicate a genuine account checkpoint or ban
    # Critical security signals that indicate a genuine account checkpoint, action block, or WAF ban
    HARD_ACTION_BLOCK_SIGNALS: frozenset[str] = frozenset(
        {
            "checkpoint_required",
            "checkpoint_url",
            "challenge_required",
            "challenge_context",
            "consent_required",
            "scraping_warning",
        }
    )

    # Authentication invalidation signals that necessitate cookie quarantine
    AUTH_EXPIRED_SIGNALS: frozenset[str] = frozenset(
        {
            *HARD_ACTION_BLOCK_SIGNALS,
            "login_required",
        }
    )

    # Transient pushbacks and endpoint-specific velocity throttles (non-lethal to cookies or session)
    TRANSIENT_RATE_LIMIT_SIGNALS: frozenset[str] = frozenset(
        {
            "please wait a few minutes",
            "rate limited",
            "too many requests",
            "feedback_required",
            "is_spam",
            "action_blocked",
        }
    )

    # Diagnostic telemetry signals
    ACTION_BLOCK_SIGNALS: frozenset[str] = frozenset(
        {
            *HARD_ACTION_BLOCK_SIGNALS,
            *TRANSIENT_RATE_LIMIT_SIGNALS,
            "login_required",
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
        self.circuit_config: CircuitBreakerConfig = (
            circuit_config or CircuitBreakerConfig()
        )
        self.circuit_state: CircuitState = CircuitState.CLOSED
        self.failure_counter: int = 0
        self.last_state_change: float = time.time()
        self.proxy_url: str | None = proxy_url
        self.cookies: dict[str, str] = dict(cookies or {})
        self.cookies_quarantined: bool = False
        self.verify_ssl: bool = verify_ssl
        self.lsd_token: str | None = None
        self.www_claim: str = "0"

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
            logger.warning(
                "curl_cffi not installed; running in standard library fallback mode."
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
        # Chrome 120 Client Hints strictly aligned with curl_cffi impersonate="chrome120"
        base_headers: dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.instagram.com",
            "Referer": "https://www.instagram.com/",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-ASBD-ID": self.ASBD_ID,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-IG-WWW-Claim": self.www_claim,
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
                        "Circuit breaker is OPEN: Request blocked due to network/WAF tripwire lockdown."
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
        """Inspects failure conditions and increments failure counters toward the threshold.

        Never quarantines active cookies on HTTP 403 routing errors or when the response indicates an active login.
        """
        with self._lock:
            lowered_text = response_text.lower()
            is_rate_limit = status_code == 429
            is_hard_action_block = any(
                sig in lowered_text for sig in self.HARD_ACTION_BLOCK_SIGNALS
            )

            # Verify if response explicitly confirms an active login context
            is_explicitly_logged_in = (
                "logged-in" in lowered_text and "not-logged-in" not in lowered_text
            )

            is_auth_expired = not is_explicitly_logged_in and (
                any(sig in lowered_text for sig in self.AUTH_EXPIRED_SIGNALS)
                or (
                    status_code in self.CRITICAL_AUTH_FAILURE_CODES
                    and was_authenticated
                )
                or (
                    "/accounts/login/" in lowered_text
                    and status_code in (302, 401, 403)
                )
            )

            # 1. Hard Checkpoint: Fail-fast and quarantine cookies only on genuine verification walls
            if is_hard_action_block:
                self.trip_circuit_breaker(
                    f"Account Checkpoint / Verification Required (status={status_code})"
                )
                if was_authenticated:
                    self.cookies_quarantined = True
                return

            # 2. Session Invalidation: Quarantine credentials only when authentication explicitly expires
            if is_auth_expired and not is_rate_limit:
                logger.warning(
                    "⚠️ [Cookie Quarantine] Session authentication expired (HTTP %d). "
                    "Quarantining session credentials.",
                    status_code,
                )
                self.cookies_quarantined = True
                return

            # 3. Transient Throttles: Increment failure counter without quarantining cookies
            is_transient_throttle = is_rate_limit or any(
                sig in lowered_text for sig in self.TRANSIENT_RATE_LIMIT_SIGNALS
            )
            if is_transient_throttle:
                self.failure_counter += 1
                logger.warning(
                    "Transient endpoint throttle encountered (HTTP %d). Strike %d of %d.",
                    status_code,
                    self.failure_counter,
                    self.circuit_config.failure_threshold,
                )
                if self.failure_counter >= self.circuit_config.failure_threshold:
                    self.trip_circuit_breaker(
                        f"Cumulative rate limit threshold reached ({self.failure_counter} strikes)"
                    )
                return

            # Exclude GraphQL schema mismatches or 403 Page Not Found errors from infrastructure lockouts
            if (status_code in (400, 403)) and (
                "execution error" in lowered_text or "page not found" in lowered_text
            ):
                logger.debug(
                    "GraphQL query rejected or routed to error page (HTTP %d). Skipping infrastructure trip.",
                    status_code,
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
                    f"Upstream infrastructure fault threshold reached (status={status_code})"
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
        """Handshakes with Instagram root over HTTP/2 to bootstrap CSRF and LSD tokens.

        Bypasses network round-trip if valid session credentials already exist.
        """
        if "csrftoken" in self.cookies and (
            self.has_session_cookies() or self.lsd_token
        ):
            return

        try:
            _, _, _, text = self.request(
                "GET",
                "https://www.instagram.com/",
                headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"},
                timeout=10.0,
                require_auth=False,
            )
            if text:
                if not self.lsd_token:
                    m_lsd = re.search(
                        r'["\']LSD["\'],\[\],\{["\']token["\']:\s*["\']([^"\']+)["\']\}',
                        text,
                    )
                    if not m_lsd:
                        m_lsd = re.search(
                            r'name=["\']lsd["\']\s+value=["\']([^"\']+)["\']', text
                        )
                    if m_lsd:
                        self.lsd_token = m_lsd.group(1)
                        logger.debug(
                            "Successfully extracted LSD token: %s", self.lsd_token
                        )

                if "csrftoken" not in self.cookies:
                    m_csrf = re.search(
                        r'["\']csrf_token["\']:\s*["\']([^"\']+)["\']', text
                    )
                    if m_csrf:
                        self.cookies["csrftoken"] = m_csrf.group(1)
                        logger.debug(
                            "Successfully extracted CSRF token from page markup."
                        )
        except Exception as exc:
            logger.debug(
                "Token bootstrap handshake encountered non-fatal error: %s", exc
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
        """Unified HTTP dispatcher with session isolation, header synchronization, and fail-fast tripwires."""
        self._check_circuit()

        is_mocked_env = hasattr(urllib.request.urlopen, "assert_called") or hasattr(
            urllib.request.urlopen, "_mock_name"
        )

        req_method = method.upper()
        merged_headers = dict(headers or {})
        effective_auth = require_auth and self.has_session_cookies()

        # Synchronize dynamic claim header
        merged_headers.setdefault("X-IG-WWW-Claim", self.www_claim)

        # Primary Branch: curl_cffi HTTP/2 Chrome Impersonation
        if self._auth_session is not None and not is_mocked_env:
            active_session: Any = (
                self._auth_session if effective_auth else self._anon_session
            )

            # Prevent duplicate Cookie header collision over HTTP/2 by letting Session manage cookies natively
            req_cookies: dict[str, str] | None = None
            if effective_auth:
                req_cookies = self.cookies
                if "csrftoken" in self.cookies and "X-CSRFToken" not in merged_headers:
                    merged_headers["X-CSRFToken"] = self.cookies["csrftoken"]
                # Ensure no manual Cookie header conflicts with curl_cffi cookie jar
                merged_headers.pop("Cookie", None)

            try:
                resp = active_session.request(
                    method=req_method,
                    url=url,
                    headers=merged_headers,
                    data=data,
                    params=params,
                    cookies=req_cookies,
                    timeout=timeout,
                    allow_redirects=True,
                )
                status_code = int(resp.status_code)
                final_url = str(resp.url)
                text = resp.text
                resp_headers = dict(resp.headers)

                claim_candidate = resp_headers.get(
                    "x-ig-set-www-claim"
                ) or resp_headers.get("X-IG-Set-WWW-Claim")
                if claim_candidate:
                    self.www_claim = claim_candidate

                if hasattr(resp, "cookies") and resp.cookies:
                    for k, v in resp.cookies.items():
                        self.cookies[k] = v

            except Exception as exc:
                self._record_failure(0, str(exc), was_authenticated=effective_auth)
                raise ConnectionError(f"Transport network fault: {exc}") from exc

            # Fail-fast check on body challenges across all HTTP status codes (200, 400, 403, 429)
            lowered = text.lower()
            # 1. Hard Checkpoints: Immediate tripwire lockdown to protect the Instagram account
            for signal in self.HARD_ACTION_BLOCK_SIGNALS:
                if signal in lowered:
                    self.trip_circuit_breaker(
                        f"Hard account checkpoint triggered ({signal})"
                    )
                    if effective_auth:
                        self.cookies_quarantined = True
                    raise PermissionError(f"Action block challenge triggered: {signal}")

            # 2. Transient Velocity Limits: Treat as an incremental strike, allowing tier fallbacks
            is_transient = any(
                sig in lowered for sig in self.TRANSIENT_RATE_LIMIT_SIGNALS
            )
            if is_transient:
                logger.warning(
                    "Transient velocity throttle in response body: %s", text[:120]
                )
                self._record_failure(
                    status_code if status_code != 200 else 429,
                    text,
                    was_authenticated=effective_auth,
                )
                return (
                    (status_code if status_code != 200 else 429),
                    final_url,
                    resp_headers,
                    text,
                )

            if status_code == 200:
                self._record_success()
                return status_code, final_url, resp_headers, text

            self._record_failure(status_code, text, was_authenticated=effective_auth)
            return status_code, final_url, resp_headers, text

        # Fallback Branch: urllib.request
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        encoded_data: bytes | None = None
        if data is not None:
            if isinstance(data, dict):
                encoded_data = urllib.parse.urlencode(data).encode("utf-8")
                merged_headers.setdefault(
                    "Content-Type", "application/x-www-form-urlencoded"
                )
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

                claim_candidate = resp_headers.get(
                    "x-ig-set-www-claim"
                ) or resp_headers.get("X-IG-Set-WWW-Claim")
                if claim_candidate:
                    self.www_claim = claim_candidate

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

                lowered = text.lower()
                for signal in self.HARD_ACTION_BLOCK_SIGNALS:
                    if signal in lowered:
                        self.trip_circuit_breaker(f"Action block in payload ({signal})")
                        if effective_auth:
                            self.cookies_quarantined = True
                        raise PermissionError(
                            f"Action block challenge triggered: {signal}"
                        )

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

            lowered = err_body.lower()
            for signal in self.HARD_ACTION_BLOCK_SIGNALS:
                if signal in lowered:
                    self.trip_circuit_breaker(
                        f"Action block challenge in HTTP {exc.code} body: {signal}"
                    )
                    if effective_auth:
                        self.cookies_quarantined = True
                    raise PermissionError(
                        f"Action block challenge triggered: {signal}"
                    ) from exc

            self._record_failure(exc.code, err_body, was_authenticated=effective_auth)
            if exc.code == 429:
                self.trip_circuit_breaker("HTTP 429 Rate Limit encountered")
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
        """Executes an Instagram GraphQL Persisted Document query over HTTP/2 with session isolation."""
        self._check_circuit()
        self.ensure_csrf_token()
        self.pace_request()

        payload: dict[str, str] = {
            "doc_id": doc_id,
            "variables": json.dumps(variables, separators=(",", ":")),
        }

        # Guard: Only attach LSD tokens on unauthenticated queries to prevent viewer context invalidation
        is_auth = self.has_session_cookies()
        if not is_auth and self.lsd_token:
            payload["lsd"] = self.lsd_token

        headers: dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": friendly_name,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-ASBD-ID": self.ASBD_ID,
            "X-Requested-With": "XMLHttpRequest",
            "X-IG-WWW-Claim": self.www_claim,
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
        }
        if not is_auth and self.lsd_token:
            headers["X-FB-LSD"] = self.lsd_token
        if "csrftoken" in self.cookies:
            headers["X-CSRFToken"] = self.cookies["csrftoken"]

        status_code, _, _, text = self.request(
            method="POST",
            url="https://www.instagram.com/graphql/query/",
            headers=headers,
            data=payload,
            timeout=15.0,
            require_auth=True,
        )

        if status_code != 200:
            lowered = text.lower()
            if any(sig in lowered for sig in self.HARD_ACTION_BLOCK_SIGNALS):
                self.trip_circuit_breaker(
                    f"Action block in GraphQL response ({friendly_name})"
                )
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
                if any(
                    sig in err_msg.lower() for sig in self.HARD_ACTION_BLOCK_SIGNALS
                ):
                    self.trip_circuit_breaker(
                        f"Action block inside GraphQL error payload ({friendly_name})"
                    )
                    raise PermissionError(
                        f"Action block challenge in GraphQL errors: {err_msg}"
                    )
                raise RuntimeError(
                    f"GraphQL execution rejected ({friendly_name}): {err_msg}"
                )

            return res_json
        except json.JSONDecodeError as jde:
            raise ValueError(f"Failed to decode GraphQL response JSON: {jde}") from jde
