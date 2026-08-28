"""
core/cookie_manager.py - Resilient Instagram cookie importer and parser.
Supports Netscape cookies.txt, browser-extension JSON formats, and raw Cookie header strings.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CookieManager:
    """
    Manages Instagram session cookies, importing Netscape cookies.txt, browser-extension JSON,
    or raw header strings, standardizing them into both Netscape format files for yt-dlp
    and clean semicolon-separated header strings for direct API requests.
    """

    def __init__(self, storage_dir: str = "config"):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)
        self.cookie_file_path = os.path.join(self.storage_dir, "instagram_cookies.txt")
        self._cookie_string: str = ""
        self._load_stored_cookie()

    def _load_stored_cookie(self) -> None:
        """Loads and parses existing cookie file if available."""
        if os.path.exists(self.cookie_file_path):
            try:
                with open(
                    self.cookie_file_path, "r", encoding="utf-8", errors="replace"
                ) as f:
                    content = f.read().strip()
                header_str, _ = self._parse_cookie_data(content)
                self._cookie_string = header_str
            except Exception as e:
                logger.debug(f"Failed to load stored cookies: {e}")
                self._cookie_string = ""

    def _parse_cookie_data(self, raw_content: str) -> Tuple[str, str]:
        """
        Parses raw cookie data from various formats (JSON, Netscape, raw header string)
        and returns a tuple of (header_string, netscape_file_content).
        """
        raw_content = raw_content.strip()
        if not raw_content:
            return "", ""

        # Cookie record: (domain, include_subdomains, path, secure, expiration, name, value)
        cookies: List[Tuple[str, str, str, str, str, str, str]] = []

        # 1. Try parsing as JSON (EditThisCookie, Cookie-Editor, etc.)
        if (raw_content.startswith("[") and raw_content.endswith("]")) or (
            raw_content.startswith("{") and raw_content.endswith("}")
        ):
            try:
                data = json.loads(raw_content)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            name = str(
                                item.get("name") or item.get("key") or ""
                            ).strip()
                            val = str(item.get("value") or "").strip()
                            if name:
                                domain = str(
                                    item.get("domain") or ".instagram.com"
                                ).strip()
                                path = str(item.get("path") or "/").strip()
                                secure = "TRUE" if item.get("secure", True) else "FALSE"
                                exp_val = (
                                    item.get("expirationDate")
                                    or item.get("expires")
                                    or 2147483647
                                )
                                exp = str(int(float(exp_val)))
                                flag = "TRUE" if domain.startswith(".") else "FALSE"
                                cookies.append(
                                    (domain, flag, path, secure, exp, name, val)
                                )
                elif isinstance(data, dict):
                    if "cookies" in data and isinstance(data["cookies"], list):
                        for item in data["cookies"]:
                            if isinstance(item, dict):
                                name = str(
                                    item.get("name") or item.get("key") or ""
                                ).strip()
                                val = str(item.get("value") or "").strip()
                                if name:
                                    domain = str(
                                        item.get("domain") or ".instagram.com"
                                    ).strip()
                                    path = str(item.get("path") or "/").strip()
                                    secure = (
                                        "TRUE" if item.get("secure", True) else "FALSE"
                                    )
                                    exp_val = (
                                        item.get("expirationDate")
                                        or item.get("expires")
                                        or 2147483647
                                    )
                                    exp = str(int(float(exp_val)))
                                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                                    cookies.append(
                                        (domain, flag, path, secure, exp, name, val)
                                    )
                    else:
                        for k, v in data.items():
                            name = str(k).strip()
                            val = str(v).strip()
                            if name and not isinstance(v, (dict, list)):
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

        # 2. Try Netscape format (tab-separated)
        if not cookies and ("\t" in raw_content or raw_content.startswith("#")):
            for line in raw_content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain = parts[0].strip()
                    flag = parts.strip()
                    path = parts[2].strip()
                    secure = parts[3].strip()
                    expiry = parts[4].strip()
                    name = parts[5].strip()
                    val = parts[6].strip()
                    if name:
                        cookies.append((domain, flag, path, secure, expiry, name, val))

        # 3. Try raw header string / key-value format (key=value; key2=val2)
        if not cookies:
            pairs = raw_content.split(";")
            for p in pairs:
                p = p.strip()
                if not p:
                    continue
                if "=" in p:
                    name, val = p.split("=", 1)
                    name = name.strip()
                    val = val.strip()
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

        # Semicolon-separated cookie header for HTTP requests
        header_pairs = [f"{c[5]}={c[6]}" for c in cookies]
        header_str = "; ".join(header_pairs)

        # Standard Netscape cookies.txt file for yt-dlp
        netscape_lines = [
            "# Netscape HTTP Cookie File",
            "# http://curl.haxx.se/rfc/cookie_spec.html",
            "# This file was generated by Instagram Pro Downloader.",
            "",
        ]
        for c in cookies:
            netscape_lines.append(
                f"{c[0]}\t{c}\t{c[2]}\t{c[3]}\t{c[4]}\t{c[5]}\t{c[6]}"
            )
        netscape_content = "\n".join(netscape_lines) + "\n"

        return header_str, netscape_content

    def import_cookie_file(self, src_path: str) -> bool:
        """
        Imports cookie from a file (Netscape .txt, JSON, or text),
        parses into standard formats, and writes to config storage.
        """
        if not src_path or not os.path.exists(src_path):
            return False
        try:
            with open(src_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            header_str, netscape_content = self._parse_cookie_data(content)
            if not header_str:
                return False

            with open(self.cookie_file_path, "w", encoding="utf-8") as f:
                f.write(netscape_content)

            self._cookie_string = header_str
            return True
        except Exception as e:
            logger.debug(f"Cookie import failed: {e}")
            return False

    def import_cookie_string(self, raw_str: str) -> bool:
        """Imports raw cookie string or JSON string directly."""
        if not raw_str:
            return False
        try:
            header_str, netscape_content = self._parse_cookie_data(raw_str)
            if not header_str:
                return False
            with open(self.cookie_file_path, "w", encoding="utf-8") as f:
                f.write(netscape_content)
            self._cookie_string = header_str
            return True
        except Exception as e:
            logger.debug(f"Cookie string import failed: {e}")
            return False

    def get_cookie_string(self) -> str:
        """Returns the cookie header string (k1=v1; k2=v2)."""
        return self._cookie_string

    def get_cookie_file_path(self) -> str:
        """Returns the path to the valid Netscape cookie file if it exists."""
        return (
            self.cookie_file_path
            if (os.path.exists(self.cookie_file_path) and self._cookie_string)
            else ""
        )

    def has_cookies(self) -> bool:
        """Checks if valid cookies are currently loaded."""
        return bool(self._cookie_string)

    def get_csrf_token(self) -> Optional[str]:
        """Extracts csrftoken from current cookie string."""
        if not self._cookie_string:
            return None
        m = re.search(r"(?:^|;\s*|\b)csrftoken=([^;]+)", self._cookie_string)
        return m.group(1) if m else None

    def get_user_id(self) -> Optional[str]:
        """Extracts ds_user_id from current cookie string."""
        if not self._cookie_string:
            return None
        m = re.search(r"(?:^|;\s*|\b)ds_user_id=([^;]+)", self._cookie_string)
        return m.group(1) if m else None

    def clear_cookies(self) -> None:
        """Deletes stored cookie file and resets state."""
        if os.path.exists(self.cookie_file_path):
            try:
                os.remove(self.cookie_file_path)
            except Exception:
                pass
        self._cookie_string = ""
