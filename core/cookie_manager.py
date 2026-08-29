"""
core/cookie_manager.py - Resilient Instagram cookie importer, manager, and parser.
Supports Netscape cookies.txt (tab or space delimited, #HttpOnly_ prefixes),
DevTools table exports, browser-extension JSON formats, cURL commands, and raw Cookie header strings.
Guarantees strict compliance with Python's http.cookiejar and yt-dlp cookie specifications.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CookieManager:
    """
    Manages Instagram session cookies, importing Netscape cookies.txt, browser-extension JSON,
    DevTools table exports, cURL commands, or raw header strings, standardizing them into both
    Netscape format files for yt-dlp and clean semicolon-separated header strings for direct API requests.
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
                content = self._read_file_content(self.cookie_file_path)
                header_str, _ = self._parse_cookie_data(content)
                self._cookie_string = header_str
            except Exception as e:
                logger.debug(f"Failed to load stored cookies: {e}")
                self._cookie_string = ""

    def _read_file_content(self, path: str) -> str:
        """Reads file content safely handling UTF-8 with BOM, UTF-16 LE/BE, ANSI, etc."""
        if not path or not os.path.exists(path):
            return ""
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read()
            for enc in (
                "utf-8-sig",
                "utf-16",
                "utf-16-le",
                "utf-16-be",
                "utf-8",
                "latin-1",
                "cp1252",
            ):
                try:
                    return raw_bytes.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.debug(f"File read failed for {path}: {e}")
            return ""

    def _parse_cookie_data(self, raw_content: str) -> Tuple[str, str]:
        """
        Parses raw cookie data from various formats (JSON, Netscape, DevTools table, raw header, cURL, etc.)
        and returns a tuple of (header_string, netscape_file_content).
        Strictly aligns Netscape domain prefixes and flags to satisfy http.cookiejar (domain_specified == initial_dot).
        """
        if not raw_content:
            return "", ""
        raw_content = raw_content.strip().lstrip("\ufeff")
        if not raw_content:
            return "", ""

        # 1. Extract from cURL command if present
        curl_match = re.search(
            r'(?:-H|--header)\s+[\'"](?:cookie|Cookie):\s*([^\'"]+)[\'"]',
            raw_content,
            re.IGNORECASE,
        )
        if curl_match:
            raw_content = curl_match.group(1).strip()
        else:
            curl_b = re.search(r'(?:-b|--cookie)\s+[\'"]([^\'"]+)[\'"]', raw_content)
            if curl_b:
                raw_content = curl_b.group(1).strip()

        # Cookie tuple: (domain, include_subdomains, path, secure, expiration, name, value)
        cookies: List[Tuple[str, str, str, str, str, str, str]] = []

        # 2. Try JSON parsing (EditThisCookie, Cookie-Editor, array of objects, dicts)
        trimmed = raw_content.strip()
        if (trimmed.startswith("[") and trimmed.endswith("]")) or (
            trimmed.startswith("{") and trimmed.endswith("}")
        ):
            try:
                try:
                    data = json.loads(trimmed)
                except Exception:
                    data = ast.literal_eval(trimmed)

                def _extract_cookie_obj(
                    item: Any,
                ) -> Optional[Tuple[str, str, str, str, str, str, str]]:
                    if not isinstance(item, dict):
                        return None
                    name = str(
                        item.get("name") or item.get("key") or item.get("Name") or ""
                    ).strip()
                    val = str(
                        item.get("value") or item.get("val") or item.get("Value") or ""
                    ).strip()
                    if not name:
                        return None
                    raw_domain = str(
                        item.get("domain") or item.get("Domain") or ".instagram.com"
                    ).strip()
                    path = str(item.get("path") or item.get("Path") or "/").strip()
                    sec = item.get("secure", item.get("Secure", True))
                    secure = (
                        "TRUE" if str(sec).lower() in ("true", "1", "yes") else "FALSE"
                    )
                    exp_val = (
                        item.get("expirationDate")
                        or item.get("expires")
                        or item.get("Expires")
                        or 2147483647
                    )
                    try:
                        exp = str(int(float(exp_val)))
                    except Exception:
                        exp = "2147483647"

                    domain = (
                        "." + raw_domain.lstrip(".")
                        if not raw_domain.startswith("www.")
                        else raw_domain.lstrip(".")
                    )
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    return (domain, flag, path, secure, exp, name, val)

                if isinstance(data, list):
                    for item in data:
                        c = _extract_cookie_obj(item)
                        if c:
                            cookies.append(c)
                elif isinstance(data, dict):
                    if isinstance(data.get("cookies"), list):
                        for item in data["cookies"]:
                            c = _extract_cookie_obj(item)
                            if c:
                                cookies.append(c)
                    elif "name" in data and ("value" in data or "val" in data):
                        c = _extract_cookie_obj(data)
                        if c:
                            cookies.append(c)
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

        # 3. Line-by-line parsing (Netscape, DevTools Table, 2-column key-value)
        if not cookies:
            for raw_line in raw_content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Strip Netscape #HttpOnly_ prefixes (case-insensitive)
                if re.match(r"^#\s*httponly_", line, re.IGNORECASE):
                    line = re.sub(
                        r"^#\s*httponly_", "", line, flags=re.IGNORECASE
                    ).strip()
                elif line.startswith("#"):
                    continue

                parts = line.split("\t") if "\t" in line else line.split(None, 6)
                if len(parts) >= 6:
                    p0, p1, p2, p3, p4, p5 = [p.strip() for p in parts[:6]]
                    p6 = parts.strip() if len(parts) >= 7 else ""

                    is_flag1 = p1.upper() in ("TRUE", "FALSE", "1", "0", "YES", "NO")
                    is_flag3 = p3.upper() in ("TRUE", "FALSE", "1", "0", "YES", "NO")

                    # Case A: Netscape format (Domain, Subdomains, Path, Secure, Expires, Name, Value)
                    if (
                        is_flag1 or is_flag3 or "." in p0 or p0.startswith(".")
                    ) and not ("." in p2 and "." not in p0):
                        raw_domain = p0 if p0 else ".instagram.com"
                        if raw_domain.startswith("."):
                            domain = raw_domain
                            flag = "TRUE"
                        elif any(
                            d in raw_domain.lower()
                            for d in (
                                "instagram.com",
                                "fbcdn.net",
                                "facebook.com",
                                "meta.com",
                            )
                        ) and not raw_domain.startswith("www."):
                            domain = "." + raw_domain.lstrip(".")
                            flag = "TRUE"
                        else:
                            domain = raw_domain.lstrip(".")
                            flag = "FALSE"

                        path = p2 if p2 else "/"
                        secure = (
                            "TRUE" if p3.upper() in ("TRUE", "1", "YES") else "FALSE"
                        )
                        try:
                            exp_int = int(float(p4))
                            expiry = str(exp_int) if exp_int > 0 else "2147483647"
                        except Exception:
                            expiry = "2147483647"
                        name = p5
                        val = p6
                        if name:
                            cookies.append(
                                (domain, flag, path, secure, expiry, name, val)
                            )
                            continue

                    # Case B: DevTools Table format (Name, Value, Domain, Path, Expires, ...)
                    if len(parts) >= 3 and (
                        "." in p2 or "instagram" in p2.lower() or p2.startswith(".")
                    ):
                        name = p0
                        val = p1
                        raw_domain = p2 if p2 else ".instagram.com"
                        domain = (
                            "." + raw_domain.lstrip(".")
                            if not raw_domain.startswith("www.")
                            else raw_domain.lstrip(".")
                        )
                        path = p3 if len(parts) > 3 and p3 else "/"
                        flag = "TRUE" if domain.startswith(".") else "FALSE"
                        secure = "TRUE"
                        expiry = "2147483647"
                        if name and val:
                            cookies.append(
                                (domain, flag, path, secure, expiry, name, val)
                            )
                            continue

                # Case C: 2-column Tab/Space separated (Name, Value)
                if len(parts) == 2 and not line.startswith(("http:", "https:")):
                    name, val = parts[0].strip(), parts.strip()
                    if name and val and "=" not in name and not name.startswith("#"):
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
                        continue

        # 4. Fallback: Raw Cookie header string or key-value pairs (semicolon, newline, colon, or ampersand delimited)
        if not cookies:
            cleaned = re.sub(
                r"^(?:cookie|Cookie):\s*", "", raw_content, flags=re.IGNORECASE
            ).strip()
            pair_matches = re.findall(r"([a-zA-Z0-9_\-\.]+)\s*=\s*([^;\r\n]+)", cleaned)
            for name, val in pair_matches:
                name = name.strip()
                val = val.strip().strip('"').strip("'")
                if name and not name.lower().startswith(
                    ("http", "https", "curl", "user-agent", "accept")
                ):
                    cookies.append(
                        (".instagram.com", "TRUE", "/", "TRUE", "2147483647", name, val)
                    )

        if not cookies:
            return "", ""

        # Filter/prioritize Instagram/Meta cookies if multi-domain file
        ig_cookies = [
            c
            for c in cookies
            if any(
                d in c[0].lower()
                for d in ("instagram.com", "fbcdn.net", "facebook.com", "meta.com")
            )
            or c.lower()
            in (
                "sessionid",
                "ds_user_id",
                "csrftoken",
                "mid",
                "rur",
                "ig_did",
                "datr",
                "shbid",
                "shbts",
            )
        ]
        selected_cookies = ig_cookies if ig_cookies else cookies

        # Deduplicate preserving last value
        dedup: Dict[str, Tuple[str, str, str, str, str, str, str]] = {}
        for c in selected_cookies:
            dedup[c[5]] = c
        unique_cookies = list(dedup.values())

        # Semicolon-separated cookie header for HTTP requests
        header_pairs = [
            f"{c_name}={c_val}"
            for (c_dom, c_flag, c_path, c_sec, c_exp, c_name, c_val) in unique_cookies
        ]
        header_str = "; ".join(header_pairs)

        # Standard Netscape cookies.txt file for yt-dlp & MozillaCookieJar
        netscape_lines = [
            "# Netscape HTTP Cookie File",
            "# http://curl.haxx.se/rfc/cookie_spec.html",
            "# This file was generated by Instagram Pro Downloader.",
            "",
        ]
        for c_dom, c_flag, c_path, c_sec, c_exp, c_name, c_val in unique_cookies:
            dom = str(c_dom).strip()
            # Guarantee strict compliance: domain.startswith('.') MUST equal (flag == 'TRUE')
            if dom.startswith("."):
                domain = dom
                flag = "TRUE"
            elif any(
                d in dom.lower()
                for d in ("instagram.com", "fbcdn.net", "facebook.com", "meta.com")
            ) and not dom.startswith("www."):
                domain = "." + dom.lstrip(".")
                flag = "TRUE"
            else:
                domain = dom.lstrip(".")
                flag = "FALSE"

            path = str(c_path).strip() if str(c_path).strip() else "/"
            secure = "TRUE" if str(c_sec).upper() in ("TRUE", "1", "YES") else "FALSE"
            exp_str = str(c_exp).strip()
            expiry = exp_str if exp_str.isdigit() else "2147483647"
            name = str(c_name).strip()
            val = str(c_val).strip()
            netscape_lines.append(
                f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{val}"
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
            content = self._read_file_content(src_path)
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
        """Checks if any cookies are currently loaded."""
        return bool(self._cookie_string)

    def has_authenticated_cookies(self) -> bool:
        """Checks if authenticated session cookies (sessionid) are loaded."""
        return bool(self.get_session_id())

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

    def get_session_id(self) -> Optional[str]:
        """Extracts sessionid from current cookie string."""
        if not self._cookie_string:
            return None
        m = re.search(r"(?:^|;\s*|\b)sessionid=([^;]+)", self._cookie_string)
        return m.group(1) if m else None

    def clear_cookies(self) -> None:
        """Deletes stored cookie file and resets state."""
        if os.path.exists(self.cookie_file_path):
            try:
                os.remove(self.cookie_file_path)
            except Exception:
                pass
        self._cookie_string = ""
