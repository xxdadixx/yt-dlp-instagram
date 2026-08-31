"""
core/cookie_manager.py - Resilient Instagram cookie importer, manager, and parser.
Standardizes Netscape cookies.txt, browser-extension JSON, and raw HTTP header strings.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import sys
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CookieManager:
    DEFAULT_COOKIE_FILE = os.path.join("config", "instagram_cookies.txt")

    def __init__(self, cookie_file: Optional[str] = None):
        self.cookies: Dict[str, str] = {}
        self._cookie_string: str = ""

        if cookie_file:
            self.cookie_file_path = os.path.abspath(cookie_file)
        else:
            # Resolve relative to project root instead of runtime CWD
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.cookie_file_path = os.path.join(
                project_root, "config", "instagram_cookies.txt"
            )

        if os.path.exists(self.cookie_file_path):
            self.load_from_file(self.cookie_file_path)

    def load_from_file(self, file_path: str) -> bool:
        """Loads cookies from a specified file path."""
        return self.import_cookie_file(file_path)

    def _read_file_content(self, path: str) -> str:
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
        if not raw_content:
            return "", ""
        raw_content = raw_content.strip().lstrip("\ufeff")
        if not raw_content:
            return "", ""

        cookies: List[Tuple[str, str, str, str, str, str, str]] = []

        # 1. JSON structure
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

        # 2. Netscape / Tab-delimited parsing
        if not cookies:
            for raw_line in raw_content.splitlines():
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
                    p0, p1, p2, p3, p4, p5 = [p.strip() for p in parts[:6]]
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

        # 3. Raw header parsing fallback
        if not cookies:
            cleaned = re.sub(
                r"^(?:cookie|Cookie):\s*", "", raw_content, flags=re.IGNORECASE
            ).strip()
            pair_matches = re.findall(r"([a-zA-Z0-9_\-\.]+)\s*=\s*([^;\r\n]+)", cleaned)
            for name, val in pair_matches:
                name = name.strip()
                val = val.strip().strip('"').strip("'")
                if name:
                    cookies.append(
                        (".instagram.com", "TRUE", "/", "TRUE", "2147483647", name, val)
                    )

        if not cookies:
            return "", ""

        # Deduplicate
        dedup: Dict[str, Tuple[str, str, str, str, str, str, str]] = {}
        for c in cookies:
            dedup[c[5]] = c
        unique_cookies = list(dedup.values())

        header_pairs = [f"{c[5]}={c[6]}" for c in unique_cookies]
        header_str = "; ".join(header_pairs)

        netscape_lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/rfc/cookie_spec.html",
            "# This file was generated by Instagram Pro Downloader.",
            "",
        ]
        for c_dom, c_flag, c_path, c_sec, c_exp, c_name, c_val in unique_cookies:
            netscape_lines.append(
                f"{c_dom}\t{c_flag}\t{c_path}\t{c_sec}\t{c_exp}\t{c_name}\t{c_val}"
            )
        netscape_content = "\n".join(netscape_lines) + "\n"

        return header_str, netscape_content

    def import_cookie_file(self, file_path: str) -> bool:
        if not file_path or not os.path.exists(file_path):
            return False

        try:
            content = self._read_file_content(file_path)
            if not content.strip():
                return False

            header_str, netscape_content = self._parse_cookie_data(content)
            if not header_str:
                return False

            self.cookies.clear()
            for pair in header_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    self.cookies[k.strip()] = v.strip()

            self._cookie_string = header_str

            os.makedirs(os.path.dirname(self.cookie_file_path), exist_ok=True)
            with open(self.cookie_file_path, "w", encoding="utf-8") as f:
                f.write(netscape_content)

            return True
        except Exception as e:
            logger.error(f"Failed to import cookie file: {e}")
            return False

    def get_cookie_string(self) -> str:
        if not self.cookies:
            return self._cookie_string
        return "; ".join([f"{k}={v}" for k, v in self.cookies.items()])

    def get_cookie_file_path(self) -> Optional[str]:
        if os.path.exists(self.cookie_file_path):
            return os.path.abspath(self.cookie_file_path)
        if self.cookies and self.save_to_netscape_file():
            return os.path.abspath(self.cookie_file_path)
        return None

    def save_to_netscape_file(self, file_path: Optional[str] = None) -> bool:
        target = file_path or self.cookie_file_path
        if not target:
            return False

        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            content = self._generate_netscape_content()
            
            # Enforce 0600 (owner-only read/write) permissions on POSIX systems
            if sys.platform != "win32":
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(target, flags, 0o600)
                with open(fd, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)

            return True
        except Exception as e:
            logger.error(f"Failed to save cookies to {target}: {e}")
            return False

    def has_cookies(self) -> bool:
        return bool(self.cookies) or bool(self._cookie_string)

    def get_csrf_token(self) -> Optional[str]:
        return self.cookies.get("csrftoken")

    def get_user_id(self) -> Optional[str]:
        return self.cookies.get("ds_user_id")

    def get_session_id(self) -> Optional[str]:
        if "sessionid" in self.cookies:
            return self.cookies["sessionid"]
        c_str = self.get_cookie_string()
        if not c_str:
            return None
        m = re.search(r"(?:^|;\s*|\b)sessionid=([^;]+)", c_str)
        return m.group(1) if m else None

    def clear_cookies(self) -> None:
        if os.path.exists(self.cookie_file_path):
            try:
                os.remove(self.cookie_file_path)
            except Exception:
                pass
        self.cookies.clear()
        self._cookie_string = ""

    def convert_cookie_string_to_netscape(
        cookie_string: str, output_filepath: str
    ) -> bool:
        """Parses a semicolon-delimited cookie string and generates a valid Netscape format file."""
        if not cookie_string or not cookie_string.strip():
            return False

        default_expiry = int(time.time()) + (365 * 24 * 60 * 60)

        parsed_cookies: Dict[str, str] = {}
        for item in cookie_string.split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            k, v = item.split("=", 1)
            parsed_cookies[k.strip()] = v.strip()

        if not parsed_cookies:
            return False

        os.makedirs(os.path.dirname(os.path.abspath(output_filepath)), exist_ok=True)

        header = (
            "# Netscape HTTP Cookie File\n"
            "# https://curl.se/rfc/cookie_spec.html\n"
            "# This file was generated by Instagram Pro Downloader.\n\n"
        )

        lines = [header]
        for name, value in parsed_cookies.items():
            expiry = "0" if name == "rur" else str(default_expiry)
            line = f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}\n"
            lines.append(line)

        try:
            with open(output_filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True
        except OSError as e:
            logger.error(f"Failed to write Netscape cookie file: {e}")
            return False
