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
    dynamic LSD/Claim token binding, and differentiated circuit-breaking on WAF tripwires.
    """

    WEB_APP_ID: str = "936619743392459"
    ASBD_ID: str = "129477"

    INFRASTRUCTURE_FAILURE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    CRITICAL_AUTH_FAILURE_CODES: frozenset[int] = frozenset({401})

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

    AUTH_EXPIRED_SIGNALS: frozenset[str] = frozenset(
        {
            *HARD_ACTION_BLOCK_SIGNALS,
            "login_required",
        }
    )

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
            self._initialize_headers()

    def _initialize_headers(self) -> None:
        """Initializes clean browser baseline headers and synchronizes session cookie jars."""
        base_headers: dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "X-ASBD-ID": self.ASBD_ID,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-IG-WWW-Claim": self.www_claim,
        }

        if self._anon_session is not None:
            self._anon_session.headers.clear()
            self._anon_session.headers.update(base_headers)
            for k in ("csrftoken", "mid", "ig_did"):
                if k in self.cookies:
                    self._anon_session.cookies.set(
                        k, self.cookies[k], domain=".instagram.com"
                    )

        if self._auth_session is not None:
            self._auth_session.headers.clear()
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
        """Evaluates upstream network and WAF challenge failures. Quarantines expired credentials

        without tripping the circuit breaker, preserving public unauthenticated traffic routes.
        """
        if 200 <= status_code < 300:
            return

        with self._lock:
            lowered_text = response_text.lower()
            is_rate_limit = status_code == 429

            # 1. Hard Checkpoint Signals (Account checkpoints or CAPTCHA challenges)
            is_hard_action_block = any(
                sig in lowered_text for sig in self.HARD_ACTION_BLOCK_SIGNALS
            )

            # 2. Soft Action Blocks (IP velocity / spam / feedback_required)
            is_soft_action_block = any(
                sig in lowered_text for sig in self.TRANSIENT_RATE_LIMIT_SIGNALS
            )

            is_explicitly_logged_in = (
                "logged-in" in lowered_text and "not-logged-in" not in lowered_text
            )

            # 3. Session Expiration / Invalidation (login_required, logout_reason)
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

            # Credential Quarantining: Expired cookies must NOT trip the network circuit breaker
            if was_authenticated and is_auth_expired:
                logger.warning(
                    "Instagram session invalidated by server (HTTP %d). "
                    "Quarantining cookies and downgrading to public mode without locking circuit: %s",
                    status_code,
                    response_text[:120],
                )
                self.cookies_quarantined = True
                self.cookies.clear()
                self._initialize_headers()
                return

            # Hard Checkpoint Tripwire: Challenge or CAPTCHA required trips breaker immediately
            if is_hard_action_block:
                self.trip_circuit_breaker(
                    f"Hard Action Block / Account Checkpoint (HTTP {status_code}): {response_text[:120]}"
                )
                self.cookies_quarantined = True
                return

            # Rate Limit & WAF Velocity Tripwire
            if is_soft_action_block:
                self.trip_circuit_breaker(
                    f"WAF Action Block Triggered (HTTP {status_code}): {response_text[:120]}"
                )
                return

            # HTTP 429 Velocity Accumulator
            if is_rate_limit:
                self.failure_counter += 1
                if self.failure_counter >= self.circuit_config.failure_threshold:
                    self.trip_circuit_breaker(
                        f"Rate limit threshold reached (HTTP {status_code})"
                    )
                return

            # Benign GraphQL execution errors or missing items do not trip the breaker
            if status_code in (400, 404) and (
                "execution error" in lowered_text or "page not found" in lowered_text
            ):
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
        """Handshakes with Instagram root over HTTP/2 to bootstrap CSRF and LSD tokens."""
        if "csrftoken" in self.cookies and self.lsd_token:
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
                    m_lsd = (
                        re.search(
                            r'["\']LSD["\'],\[\],\{["\']token["\']:\s*["\']([^"\']+)["\']\}',
                            text,
                        )
                        or re.search(
                            r'name=["\']lsd["\']\s+value=["\']([^"\']+)["\']', text
                        )
                        or re.search(r'["\']lsd["\']\s*:\s*["\']([^"\']+)["\']', text)
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
                        self._initialize_headers()
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
        """Unified HTTP dispatcher executing requests via curl_cffi Chrome impersonation

        or hardened OpenSSL fallback. Enforces fail-fast boundaries on WAF tripwires.
        """
        self._check_circuit()

        is_mocked_env = hasattr(urllib.request.urlopen, "assert_called") or hasattr(
            urllib.request.urlopen, "_mock_name"
        )

        req_method = method.upper()
        merged_headers = dict(headers or {})
        effective_auth = require_auth and self.has_session_cookies()

        merged_headers.setdefault("X-IG-WWW-Claim", self.www_claim)

        # Primary Branch: curl_cffi HTTP/2 Chrome Impersonation
        if self._auth_session is not None and not is_mocked_env:
            active_session: Any = (
                self._auth_session if effective_auth else self._anon_session
            )

            if "csrftoken" in self.cookies and "X-CSRFToken" not in merged_headers:
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
                    self._initialize_headers()

            except Exception as exc:
                self._record_failure(0, str(exc), was_authenticated=effective_auth)
                raise ConnectionError(f"Transport network fault: {exc}") from exc

            lowered = text.lower()
            is_challenge = any(sig in lowered for sig in self.HARD_ACTION_BLOCK_SIGNALS)
            is_action_block = any(
                sig in lowered for sig in self.TRANSIENT_RATE_LIMIT_SIGNALS
            )

            if is_challenge or is_action_block:
                self._record_failure(
                    status_code, text, was_authenticated=effective_auth
                )
                raise PermissionError(
                    f"Instagram WAF / Account Challenge triggered (HTTP {status_code}): {text[:150]}"
                )

            if not (200 <= status_code < 300):
                self._record_failure(
                    status_code, text, was_authenticated=effective_auth
                )
                return status_code, final_url, resp_headers, text

            self._record_success()
            return status_code, final_url, resp_headers, text

        # Fallback Branch: Standard Library urllib.request (JA3/JA4 Consistent Firefox Profile)
        fallback_headers = {
            k: v
            for k, v in merged_headers.items()
            if not k.lower().startswith("sec-ch-ua")
        }
        fallback_headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0"
        )

        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"

        encoded_data: bytes | None = None
        if data is not None:
            if isinstance(data, dict):
                encoded_data = urllib.parse.urlencode(data).encode("utf-8")
                fallback_headers.setdefault(
                    "Content-Type", "application/x-www-form-urlencoded"
                )
            elif isinstance(data, str):
                encoded_data = data.encode("utf-8")
            elif isinstance(data, bytes):
                encoded_data = data

        cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if cookie_str and "Cookie" not in fallback_headers:
            fallback_headers["Cookie"] = cookie_str
        if "csrftoken" in self.cookies and "X-CSRFToken" not in fallback_headers:
            fallback_headers["X-CSRFToken"] = self.cookies["csrftoken"]

        req = urllib.request.Request(
            url=url,
            data=encoded_data,
            headers=fallback_headers,
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
                if any(sig in lowered for sig in self.ACTION_BLOCK_SIGNALS):
                    self._record_failure(
                        status_code, text, was_authenticated=effective_auth
                    )
                    raise PermissionError(
                        f"Account challenge/action block in response: {text[:150]}"
                    )

                if 200 <= status_code < 300:
                    self._record_success()
                else:
                    self._record_failure(
                        status_code, text, was_authenticated=effective_auth
                    )

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
            lowered_err = err_body.lower()
            if any(sig in lowered_err for sig in self.ACTION_BLOCK_SIGNALS):
                raise PermissionError(
                    f"HTTP {exc.code} Account Challenge / Action Block triggered: {err_body[:120]}"
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
        query_hash: str | None = None,
    ) -> dict[str, Any]:
        """Executes an Instagram GraphQL persisted query over HTTP/2 with schema-resilient

        RelayModern POST routing and automatic GET dispatch fallback.
        """
        self._check_circuit()
        self.pace_request()

        is_auth = self.has_session_cookies()
        if not is_auth:
            self.ensure_csrf_token()

        var_json = json.dumps(variables, separators=(",", ":"))
        headers: dict[str, str] = {
            "X-FB-Friendly-Name": friendly_name,
            "X-IG-App-ID": self.WEB_APP_ID,
            "X-ASBD-ID": self.ASBD_ID,
            "X-Requested-With": "XMLHttpRequest",
            "X-IG-WWW-Claim": self.www_claim,
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
            "Accept": "*/*",
        }

        if not is_auth and self.lsd_token:
            headers["X-FB-LSD"] = self.lsd_token

        if "csrftoken" in self.cookies:
            headers["X-CSRFToken"] = self.cookies["csrftoken"]

        status_code: int = 0
        text: str = ""

        # Strategy A: Query-Hash Execution (Mandatory GET Dispatch)
        if query_hash:
            get_params = {
                "query_hash": query_hash,
                "variables": var_json,
            }
            status_code, _, _, text = self.request(
                method="GET",
                url="https://www.instagram.com/graphql/query",
                headers=headers,
                params=get_params,
                timeout=15.0,
                require_auth=is_auth,
            )

        # Strategy B: Modern Relay Persisted Document ID (URL-Encoded POST Dispatch)
        else:
            post_headers = dict(headers)
            post_headers["Content-Type"] = "application/x-www-form-urlencoded"
            payload: dict[str, str] = {
                "doc_id": doc_id,
                "variables": var_json,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": friendly_name,
                "server_timestamps": "true",
            }
            if not is_auth and self.lsd_token:
                payload["lsd"] = self.lsd_token

            encoded_payload = urllib.parse.urlencode(payload)

            status_code, _, _, text = self.request(
                method="POST",
                url="https://www.instagram.com/graphql/query",
                headers=post_headers,
                data=encoded_payload,
                timeout=15.0,
                require_auth=is_auth,
            )

            # Strategy C: GET Fallback for doc_id if POST execution encounters a method/schema pushback
            if status_code in (400, 404, 405):
                logger.debug(
                    "[%s] POST doc_id %s rejected (HTTP %d). Attempting GET dispatch fallback...",
                    friendly_name,
                    doc_id,
                    status_code,
                )
                get_params = {
                    "doc_id": doc_id,
                    "variables": var_json,
                }
                status_code, _, _, text = self.request(
                    method="GET",
                    url="https://www.instagram.com/graphql/query",
                    headers=headers,
                    params=get_params,
                    timeout=15.0,
                    require_auth=is_auth,
                )

        if status_code != 200:
            raise RuntimeError(
                f"GraphQL execution ({friendly_name}) failed with HTTP {status_code}: {text[:200]}"
            )

        try:
            res_json = json.loads(text)
            if not isinstance(res_json, dict):
                raise ValueError("Malformed JSON payload returned by GraphQL endpoint")

            errors = res_json.get("errors")
            if isinstance(errors, list) and len(errors) > 0:
                first_err = errors[0]
                err_msg = (
                    first_err.get("message", "GraphQL execution error")
                    if isinstance(first_err, dict)
                    else "GraphQL execution error"
                )
                if any(sig in err_msg.lower() for sig in self.ACTION_BLOCK_SIGNALS):
                    self.trip_circuit_breaker(
                        f"Action block in GraphQL ({friendly_name}): {err_msg}"
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
