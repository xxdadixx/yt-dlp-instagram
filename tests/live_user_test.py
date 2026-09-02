"""
tests/live_user_test.py - Live User Diagnostic CLI with Full Localization.
Directly interfaces with InspectWorker across authenticated and unauthenticated pipelines.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PyQt6.QtCore import QCoreApplication

app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from core.cookie_manager import CookieManager
from core.inspect_worker import InspectWorker

I18N = {
    "en": {
        "target": "TARGET USER",
        "scope": "SCOPE",
        "cookies": "COOKIES",
        "log_file": "LOG FILE",
        "limit": "ITEM LIMIT",
        "limit_desc": "First {limit} items",
        "limit_reached": "\n[!] Reached item limit ({limit}). Stopping inspection...",
        "finished": "\n[+] Finished. Total items gathered: {total}\n",
        "summary_title": "📊 TEST SUMMARY FOR @{username}",
        "total_items": "• Total Items Found",
        "stories": "• Stories",
        "reels": "• Reels/Videos",
        "carousels": "• Carousels",
        "photos": "• Photos",
        "alerts_title": "⚠️  DIAGNOSTIC ALERTS:",
        "no_alerts": "✅ NO ERRORS OR ANOMALIES DETECTED",
        "dump_saved": "💾 JSON Payload Dump Saved : {path}",
        "attached": "Attached ({path})",
        "none": "None (Public Mode)",
    },
    "th": {
        "target": "ผู้ใช้งานเป้าหมาย",
        "scope": "ขอบเขตการค้นหา",
        "cookies": "คุกกี้ (Cookies)",
        "log_file": "ไฟล์บันทึกการทำงาน",
        "limit": "จำกัดจำนวนสื่อ",
        "limit_desc": "{limit} รายการแรก",
        "limit_reached": "\n[!] ถึงขีดจำกัดจำนวนที่กำหนด ({limit} รายการ) แล้ว กำลังหยุดการค้นหา...",
        "finished": "\n[+] การค้นหาเสร็จสิ้น รวบรวมสื่อได้ทั้งหมด: {total} รายการ\n",
        "summary_title": "📊 สรุปผลการทดสอบสำหรับ @{username}",
        "total_items": "• จำนวนสื่อทั้งหมดที่พบ",
        "stories": "• สตอรี่ (Stories)",
        "reels": "• คลิปรีลส์/วิดีโอ (Reels/Videos)",
        "carousels": "• อัลบั้มภาพ/วิดีโอ (Carousels)",
        "photos": "• รูปภาพเดี่ยว (Photos)",
        "alerts_title": "⚠️  รายการแจ้งเตือนและข้อผิดพลาด:",
        "no_alerts": "✅ ตรวจสอบสมบูรณ์ ไม่พบข้อผิดพลาดหรือความผิดปกติ",
        "dump_saved": "💾 บันทึกไฟล์ JSON Payload เรียบร้อย : {path}",
        "attached": "เชื่อมต่อแล้ว ({path})",
        "none": "ไม่ได้เชื่อมต่อ (โหมดสาธารณะ)",
    },
}


class DualLogger:
    def __init__(self, filepath: str) -> None:
        self.terminal = sys.stdout
        self.log_file = open(filepath, "w", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.terminal.flush()
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self) -> None:
        self.terminal.flush()
        self.log_file.flush()

    def close(self) -> None:
        self.log_file.close()


class EnhancedUserTester:
    def __init__(
        self,
        username: str,
        cookie_path: Optional[str] = None,
        scope: str = "all",
        limit: int = 36,
        lang: str = "en",
    ) -> None:
        self.username = username.lstrip("@").strip()
        self.cm = CookieManager(cookie_file=cookie_path)
        self.scope = scope.lower()
        self.limit = limit
        self.lang = "th" if lang.lower() == "th" else "en"
        self.t = I18N[self.lang]
        self.results: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.worker: Optional[InspectWorker] = None

    def build_targets(self) -> List[str]:
        if self.scope in ("stories", "story"):
            return [f"https://www.instagram.com/stories/{self.username}/"]
        if self.scope in ("reels", "reel"):
            return [f"https://www.instagram.com/{self.username}/reels/"]
        return [f"https://www.instagram.com/{self.username}/"]

    def run(self, save_json: bool = True) -> None:
        log_dir = os.path.join(PROJECT_ROOT, "logs", "live_tests")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = os.path.join(log_dir, f"live_{self.username}_{timestamp}.log")
        json_path = os.path.join(log_dir, f"payload_{self.username}_{timestamp}.json")

        logger = DualLogger(log_path)
        sys.stdout = logger

        try:
            targets = self.build_targets()
            cfile = self.cm.get_cookie_file_path()
            cookie_desc = (
                self.t["attached"].format(path=cfile) if cfile else self.t["none"]
            )

            print("=======================================================")
            print(f" 🎯 {self.t['target']:<18}: @{self.username}")
            print(f" 🔍 {self.t['scope']:<18}: {self.scope.upper()}")
            print(f" 🍪 {self.t['cookies']:<18}: {cookie_desc}")
            print(f" 📁 {self.t['log_file']:<18}: {log_path}")
            if self.limit > 0:
                print(
                    f" 🛑 {self.t['limit']:<18}: {self.t['limit_desc'].format(limit=self.limit)}"
                )
            print("=======================================================")

            self.worker = InspectWorker(
                targets=targets,
                cookie_str=self.cm.get_cookie_string(),
                cookie_file=cfile,
                profile_mode="reels" if self.scope in ("reels", "reel") else "all",
                max_items_per_profile=self.limit,
            )

            self.worker.status_message.connect(self._on_status)
            self.worker.item_found.connect(self._on_item)
            self.worker.finished.connect(self._on_finished)
            self.worker.error.connect(lambda err: self.errors.append(str(err)))

            self.worker.start()
            while self.worker.isRunning():
                app.processEvents()

            self._print_report(json_path if save_json else None)

        finally:
            sys.stdout = logger.terminal
            logger.close()

    def _on_status(self, msg: str) -> None:
        print(f"[*] {msg}")
        if "⚠️" in msg:
            self.warnings.append(msg.strip())
        elif "Error" in msg or "401" in msg or "403" in msg:
            self.errors.append(msg.strip())

    def _on_item(self, item: Dict[str, Any]) -> None:
        raw_type = str(item.get("media_type") or "unknown").upper()
        if "REEL" in raw_type or "VIDEO" in raw_type:
            cat = "reels"
        elif "CAROUSEL" in raw_type:
            cat = "carousels"
        elif "STORY" in raw_type:
            cat = "stories"
        else:
            cat = "photos"

        self.results[cat].append(item)
        total_found = sum(len(items) for items in self.results.values())

        if 0 < self.limit <= total_found and self.worker:
            print(self.t["limit_reached"].format(limit=self.limit))
            self.worker.cancel()

    def _on_finished(self, total: int) -> None:
        total_discovered = sum(len(items) for items in self.results.values())
        print(self.t["finished"].format(total=total_discovered))

    def _print_report(self, json_export_path: Optional[str] = None) -> None:
        total_discovered = sum(len(items) for items in self.results.values())
        print("=======================================================")
        print(f" {self.t['summary_title'].format(username=self.username)}")
        print("=======================================================")
        print(f" {self.t['total_items']:<32}: {total_discovered}")
        print(f" {self.t['stories']:<32}: {len(self.results.get('stories', []))}")
        print(f" {self.t['reels']:<32}: {len(self.results.get('reels', []))}")
        print(f" {self.t['carousels']:<32}: {len(self.results.get('carousels', []))}")
        print(f" {self.t['photos']:<32}: {len(self.results.get('photos', []))}")
        print("-------------------------------------------------------")

        if self.errors or self.warnings:
            print(f" {self.t['alerts_title']}")
            for err in set(self.errors):
                print(f"   ❌ [ERROR]   : {err}")
            for warn in set(self.warnings):
                print(f"   ⚠️  [WARNING] : {warn}")
            print("-------------------------------------------------------")
        else:
            print(f" {self.t['no_alerts']}")
            print("-------------------------------------------------------")

        if json_export_path:
            flat_items = [item for sublist in self.results.values() for item in sublist]
            with open(json_export_path, "w", encoding="utf-8") as f:
                json.dump(flat_items, f, indent=2, ensure_ascii=False)
            print(f" {self.t['dump_saved'].format(path=json_export_path)}")
            print("-------------------------------------------------------")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Instagram Diagnostic Tester")
    parser.add_argument("username", nargs="?", default=None)
    parser.add_argument(
        "-s", "--scope", default="all", choices=["all", "stories", "reels", "posts"]
    )
    parser.add_argument("-c", "--cookie", default=None)
    parser.add_argument("-n", "--limit", type=int, default=36)
    parser.add_argument(
        "--lang", default="en", choices=["en", "th"], help="Output language"
    )
    parser.add_argument("--no-json", action="store_true")

    args = parser.parse_args()
    user = args.username or input("Enter target Instagram username: ").strip()

    if user:
        tester = EnhancedUserTester(
            username=user,
            cookie_path=args.cookie,
            scope=args.scope,
            limit=args.limit,
            lang=args.lang,
        )
        tester.run(save_json=not args.no_json)
