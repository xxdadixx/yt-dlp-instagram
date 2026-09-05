"""
core/cookie_manager.py - Hardened Instagram Cookie Importer, Parser, and Cryptographic Vault.
Features at-rest Windows DPAPI encryption, volatile session-only in-memory storage,
process-bound ephemeral bridges for yt-dlp, and DoD-style multi-pass file shredding.
"""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import TypeGuard, final

from utils.file_utils import get_app_dir, get_user_data_dir

logger = logging.getLogger(__name__)

# Magic byte header identifying DPAPI-encrypted vaults
DPAPI_MAGIC_HEADER = b"DPAPI_VAULT_V1\x00"


@final
class DATA_BLOB(ctypes.Structure):
    cbData: int = 0
    pbData: int = 0
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.c_void_p),
    ]

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    # Strictly bind Win64 pointers and return types to prevent address truncation
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

def encrypt_bytes_dpapi(data: bytes) -> bytes:
    """Encrypts byte buffer using Windows DPAPI tied to the current OS user profile."""
    if sys.platform != "win32" or not data:
        return data

    raw_buffer = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(cbData=len(data), pbData=ctypes.cast(raw_buffer, ctypes.c_void_p).value or 0)
    blob_out = DATA_BLOB(cbData=0, pbData=0)

    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    success = _crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "IG_PRO_SESSION",
        None,
        None,
        None,
        0x1,
        ctypes.byref(blob_out),
    )
    if success and blob_out.pbData:
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)
    return data


def decrypt_bytes_dpapi(ciphertext: bytes) -> bytes:
    """Decrypts DPAPI ciphertext buffer for the active Windows user."""
    if sys.platform != "win32" or not ciphertext:
        return ciphertext

    raw_buffer = ctypes.create_string_buffer(ciphertext)
    blob_in = DATA_BLOB(cbData=len(ciphertext), pbData=ctypes.cast(raw_buffer, ctypes.c_void_p).value or 0)
    blob_out = DATA_BLOB(cbData=0, pbData=0)

    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    success = _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(blob_out),
    )
    if success and blob_out.pbData:
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)
    return ciphertext


def _is_dict_obj(val: object) -> TypeGuard[dict[str, object]]:
    return isinstance(val, dict)


def _is_list_obj(val: object) -> TypeGuard[list[object]]:
    return isinstance(val, list)


@final
class CookieManager:
    """Manages parsing, volatile caching, optional DPAPI persistence, and secure token lifecycle."""

    cookies: dict[str, str]
    _cookie_string: str
    allow_disk_storage: bool
    _ephemeral_file_path: str | None
    cookie_file_path: str

    def __init__(
        self,
        cookie_file: str | None = None,
        allow_disk_storage: bool = True,
    ) -> None:
        self.cookies = {}
        self._cookie_string = ""
        self.allow_disk_storage = allow_disk_storage
        self._ephemeral_file_path = None

        # Standard OS application data vault path
        self.cookie_file_path = os.path.join(
            get_user_data_dir(), "instagram_cookies.txt"
        )

        # Register process termination hook to purge ephemeral descriptors
        _ = atexit.register(self._cleanup_ephemeral_bridge)

        # Only attempt to load persistent cookies if disk storage is enabled
        if self.allow_disk_storage:
            self._auto_load_from_candidates(cookie_file)

    def _cleanup_ephemeral_bridge(self) -> None:
        """Process cleanup hook to ensure temporary plaintext files are shredded."""
        if self._ephemeral_file_path:
            self._shred_file(self._ephemeral_file_path)
            self._ephemeral_file_path = None

    def _auto_load_from_candidates(self, explicit_file: str | None = None) -> None:
        candidate_paths: list[str] = []
        if explicit_file:
            candidate_paths.append(os.path.abspath(explicit_file))

        candidate_paths.append(self.cookie_file_path)
        candidate_paths.append(
            os.path.abspath(os.path.join(get_app_dir(), "instagram_cookies.txt"))
        )
        candidate_paths.append(
            os.path.abspath(
                os.path.join(get_app_dir(), "config", "instagram_cookies.txt")
            )
        )

        for path in candidate_paths:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                try:
                    content = self._read_file_content(path)
                    # Exclude comment-only template files
                    has_tokens = any(
                        re.search(rf"^(?!#).*\b{token}\b", content, re.MULTILINE)
                        for token in ("sessionid", "csrftoken", "ds_user_id")
                    )
                    if has_tokens and self._load_and_set_memory(content):
                        # Ensure persistent user storage is synchronized and encrypted
                        if os.path.abspath(path) != os.path.abspath(
                            self.cookie_file_path
                        ):
                            self._write_secure_file(
                                self.cookie_file_path, self._generate_netscape_content()
                            )
                        logger.info("Auto-loaded active cookies from: %s", path)
                        break
                except Exception as exc:
                    logger.debug("Could not read cookie candidate %s: %s", path, exc)

    def _load_and_set_memory(self, content: str) -> bool:
        header_str, _ = self._parse_cookie_data(content)
        if not header_str:
            return False
        self.cookies.clear()
        for pair in header_str.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                self.cookies[k.strip()] = v.strip()
        self._cookie_string = header_str
        return True

    def load_from_file(self, file_path: str) -> bool:
        """Loads and parses cookies from a target file path."""
        return self.import_cookie_file(
            file_path, persist_to_disk=self.allow_disk_storage
        )

    def _read_file_content(self, path: str) -> str:
        """Reads raw, UTF-encoded, or DPAPI-encrypted file content safely."""
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read()

            # Detect and unpack DPAPI-encrypted vault headers
            if raw_bytes.startswith(DPAPI_MAGIC_HEADER):
                ciphertext = raw_bytes[len(DPAPI_MAGIC_HEADER) :]
                raw_bytes = decrypt_bytes_dpapi(ciphertext)

            for enc in (
                "utf-8-sig",
                "utf-8",
                "utf-16",
                "utf-16-le",
                "utf-16-be",
                "latin-1",
                "cp1252",
            ):
                try:
                    return raw_bytes.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug("File read failed for %s: %s", path, e)
            return ""

    def _parse_cookie_data(self, raw_content: str) -> tuple[str, str]:
        if not raw_content:
            return "", ""
        content = raw_content.strip().lstrip("\ufeff")
        if not content:
            return "", ""

        cookies: list[tuple[str, str, str, str, str, str, str]] = []

        # 1. JSON structure parsing (Extensions & DevTools exports)
        trimmed = content.strip()
        if (trimmed.startswith("[") and trimmed.endswith("]")) or (
            trimmed.startswith("{") and trimmed.endswith("}")
        ):
            try:
                raw_json: object = json.loads(trimmed)  # pyright: ignore[reportAny]

                def _extract_cookie_obj(
                    item_dict: dict[str, object],
                ) -> tuple[str, str, str, str, str, str, str] | None:
                    def _get_val(*keys: str, default: str = "") -> str:
                        for k in keys:
                            v = item_dict.get(k)
                            if v is not None:
                                s = str(v).strip()
                                if s:
                                    return s
                        return default

                    name = _get_val("name", "key", "Name")
                    val = _get_val("value", "val", "Value")
                    if not name:
                        return None

                    raw_domain = _get_val("domain", "Domain", default=".instagram.com")
                    path = _get_val("path", "Path", default="/")

                    sec_val = item_dict.get("secure")
                    if sec_val is None:
                        sec_val = item_dict.get("Secure", True)
                    secure = (
                        "TRUE"
                        if str(sec_val).lower() in ("true", "1", "yes")
                        else "FALSE"
                    )

                    exp_val = (
                        item_dict.get("expirationDate")
                        or item_dict.get("expires")
                        or item_dict.get("Expires")
                        or 2147483647
                    )
                    try:
                        exp = str(int(float(str(exp_val))))
                    except (ValueError, TypeError):
                        exp = "2147483647"

                    domain = (
                        "." + raw_domain.lstrip(".")
                        if not raw_domain.startswith("www.")
                        else raw_domain.lstrip(".")
                    )
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    return (domain, flag, path, secure, exp, name, val)

                if _is_list_obj(raw_json):
                    for raw_item in raw_json:
                        if _is_dict_obj(raw_item):
                            c = _extract_cookie_obj(raw_item)
                            if c:
                                cookies.append(c)
                elif _is_dict_obj(raw_json):
                    cookies_list = raw_json.get("cookies")
                    if _is_list_obj(cookies_list):
                        for raw_item in cookies_list:
                            if _is_dict_obj(raw_item):
                                c = _extract_cookie_obj(raw_item)
                                if c:
                                    cookies.append(c)
                    else:
                        for k_obj, v_obj in raw_json.items():
                            name = str(k_obj).strip()
                            val = str(v_obj).strip()
                            if name and not isinstance(v_obj, (dict, list)):
                                cookies.append(
                                    (
                                        ".instagram.com",
                                        "TRUE",
                                        "/",
                                        "TRUE",
                                        "2147483647",
                                        name,
                                        val,
                                    )
                                )
            except Exception:
                pass

        # 2. Netscape / Tab-delimited parsing (#HttpOnly_ support)
        if not cookies:
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if re.match(r"^#\s*httponly_", line, re.IGNORECASE):
                    line = re.sub(
                        r"^#\s*httponly_", "", line, flags=re.IGNORECASE
                    ).strip()
                elif line.startswith("#"):
                    continue

                parts = line.split("\t") if "\t" in line else line.split(None, 6)
                if len(parts) >= 6:
                    p0 = parts[0].strip()
                    _p1 = parts[1].strip()
                    p2 = parts[2].strip()
                    p3 = parts[3].strip()
                    p4 = parts[4].strip()
                    p5 = parts[5].strip()
                    p6 = parts[6].strip() if len(parts) >= 7 else ""

                    raw_domain = p0 if p0 else ".instagram.com"
                    domain = (
                        "." + raw_domain.lstrip(".")
                        if not raw_domain.startswith("www.")
                        else raw_domain.lstrip(".")
                    )
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = p2 if p2 else "/"
                    secure = "TRUE" if p3.upper() in ("TRUE", "1", "YES") else "FALSE"
                    try:
                        exp_int = int(float(p4))
                        expiry = str(exp_int) if exp_int > 0 else "2147483647"
                    except Exception:
                        expiry = "2147483647"
                    name, val = p5, p6
                    if name:
                        cookies.append((domain, flag, path, secure, expiry, name, val))
                        continue

                if len(parts) == 2 and not line.startswith(("http:", "https:")):
                    name, val = parts[0].strip(), parts[1].strip()
                    if name and val and "=" not in name:
                        cookies.append(
                            (
                                ".instagram.com",
                                "TRUE",
                                "/",
                                "TRUE",
                                "2147483647",
                                name,
                                val,
                            )
                        )

        # 3. Raw header parsing fallback (key=value; key2=val2)
        if not cookies:
            cleaned = re.sub(
                r"^(?:cookie|Cookie):\s*", "", content, flags=re.IGNORECASE
            ).strip()
            pair_matches: list[tuple[str, str]] = re.findall(
                r"([a-zA-Z0-9_\-\.]+)\s*=\s*([^;\r\n]+)", cleaned
            )
            for raw_name, raw_val in pair_matches:
                name = raw_name.strip()
                val = raw_val.strip().strip('"').strip("'")
                if name:
                    cookies.append(
                        (
                            ".instagram.com",
                            "TRUE",
                            "/",
                            "TRUE",
                            "2147483647",
                            name,
                            val,
                        )
                    )

        if not cookies:
            return "", ""

        dedup: dict[str, tuple[str, str, str, str, str, str, str]] = {}
        for c in cookies:
            dedup[c[5]] = c
        unique_cookies = list(dedup.values())

        header_pairs = [f"{c[5]}={c[6]}" for c in unique_cookies]
        header_str = "; ".join(header_pairs)

        netscape_lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.se/rfc/cookie_spec.html",
            "# This file was generated by Instagram Pro Downloader.",
            "",
        ]
        for c_dom, c_flag, c_path, c_sec, c_exp, c_name, c_val in unique_cookies:
            netscape_lines.append(
                f"{c_dom}\t{c_flag}\t{c_path}\t{c_sec}\t{c_exp}\t{c_name}\t{c_val}"
            )
        netscape_content = "\n".join(netscape_lines) + "\n"

        return header_str, netscape_content

    def _write_secure_file(self, file_path: str, content: str) -> None:
        """Atomically writes content with DPAPI encryption (Windows) and 0600 POSIX permissions."""
        target_dir = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(target_dir, exist_ok=True)

        payload_bytes = content.encode("utf-8")
        if sys.platform == "win32":
            encrypted_payload = encrypt_bytes_dpapi(payload_bytes)
            payload_bytes = DPAPI_MAGIC_HEADER + encrypted_payload

        with tempfile.NamedTemporaryFile("wb", dir=target_dir, delete=False) as tf:
            _ = tf.write(payload_bytes)
            temp_name = tf.name

        if sys.platform != "win32":
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass

        os.replace(temp_name, file_path)

        if sys.platform != "win32":
            try:
                os.chmod(file_path, 0o600)
            except OSError:
                pass

    def _shred_file(self, target_path: str) -> None:
        """Overwrites file sectors with random noise and zero bytes before unlinking."""
        if not target_path or not os.path.isfile(target_path):
            return

        try:
            length = os.path.getsize(target_path)
            if length > 0:
                # Mode must begin with 'r', 'w', or 'a'. 'r+b' enables in-place byte overwriting.
                with open(target_path, "r+b", buffering=0) as f:
                    # Pass 1: Cryptographic pseudo-random bytes
                    f.seek(0)
                    f.write(os.urandom(length))
                    f.flush()
                    os.fsync(f.fileno())

                    # Pass 2: Zero fill
                    f.seek(0)
                    f.write(b"\x00" * length)
                    f.flush()
                    os.fsync(f.fileno())

            os.remove(target_path)
        except OSError as exc:
            logger.debug("Secure shredding fallback for %s: %s", target_path, exc)
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
            except OSError:
                pass

    def import_cookie_file(self, file_path: str, persist_to_disk: bool = True) -> bool:
        """Imports cookies from external file, caching in RAM and optionally saving to disk."""
        if not file_path or not os.path.exists(file_path):
            return False

        try:
            content = self._read_file_content(file_path)
            if not content.strip() or not self._load_and_set_memory(content):
                return False

            self.allow_disk_storage = persist_to_disk

            # Synchronize physical storage according to selected defense mode
            if persist_to_disk:
                self._write_secure_file(
                    self.cookie_file_path, self._generate_netscape_content()
                )
            else:
                # Defense B: Ensure persistent on-disk copy is shredded if present
                self._shred_file(self.cookie_file_path)

            # Re-generate active ephemeral bridge
            self._generate_ephemeral_bridge()
            return True
        except Exception as e:
            logger.error("Failed to import cookie file: %s", e)
            return False

    def _generate_ephemeral_bridge(self) -> None:
        """Constructs an ephemeral, plaintext Netscape cookie file for yt-dlp."""
        self._cleanup_ephemeral_bridge()

        if not self.has_cookies():
            return

        bridge_dir = get_user_data_dir()
        os.makedirs(bridge_dir, exist_ok=True)
        bridge_file = os.path.join(bridge_dir, f".session_bridge_{os.getpid()}.tmp")

        try:
            with open(bridge_file, "w", encoding="utf-8") as f:
                _ = f.write(self._generate_netscape_content())

            if sys.platform != "win32":
                os.chmod(bridge_file, 0o600)

            self._ephemeral_file_path = bridge_file
        except Exception as exc:
            logger.debug("Failed to construct ephemeral cookie bridge: %s", exc)

    def get_cookie_string(self) -> str:
        if not self.cookies:
            return self._cookie_string
        return "; ".join([f"{k}={v}" for k, v in self.cookies.items()])

    def get_cookie_file_path(self) -> str | None:
        """
        Returns a valid, plaintext Netscape file path for yt-dlp.
        Serves an ephemeral session bridge to ensure encrypted files on disk are not exposed.
        """
        if not self.has_cookies():
            return None

        if not self._ephemeral_file_path or not os.path.exists(
            self._ephemeral_file_path
        ):
            self._generate_ephemeral_bridge()

        return self._ephemeral_file_path

    def _generate_netscape_content(self) -> str:
        lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.se/rfc/cookie_spec.html",
            "# This file was generated by Instagram Pro Downloader.",
            "",
        ]
        default_expiry = str(int(time.time()) + (365 * 24 * 60 * 60))
        for name, value in self.cookies.items():
            expiry = "0" if name == "rur" else default_expiry
            lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}")
        return "\n".join(lines) + "\n"

    def save_to_netscape_file(self, file_path: str | None = None) -> bool:
        """Explicit export of current memory cookies to a destination Netscape file."""
        target = file_path or self.cookie_file_path
        if not target:
            return False

        try:
            content = self._generate_netscape_content()
            self._write_secure_file(target, content)
            return True
        except Exception as e:
            logger.error("Failed to save cookies to %s: %s", target, e)
            return False

    def has_cookies(self) -> bool:
        return bool(self.cookies) or bool(self._cookie_string)

    def get_csrf_token(self) -> str | None:
        return self.cookies.get("csrftoken")

    def get_user_id(self) -> str | None:
        return self.cookies.get("ds_user_id")

    def get_session_id(self) -> str | None:
        if "sessionid" in self.cookies:
            return self.cookies["sessionid"]
        c_str = self.get_cookie_string()
        if not c_str:
            return None
        m = re.search(r"(?:^|;\s*|\b)sessionid=([^;]+)", c_str)
        return m.group(1) if m else None

    def clear_cookies(self) -> None:
        """Defense C: Overwrites persistent storage with multi-pass noise, unlinks bridges, and clears memory."""
        self._shred_file(self.cookie_file_path)
        self._cleanup_ephemeral_bridge()
        self.cookies.clear()
        self._cookie_string = ""
