"""
Enhanced Live User Diagnostic CLI with Full Bilingual Localization.
"""

import argparse
import datetime
import json
import os
import re
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PyQt6.QtCore import QCoreApplication

app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from core.inspect_worker import InspectionWorker

# Bilingual Text Templates
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
    def __init__(self, filepath: str):
        self.terminal = sys.stdout
        self.log_file = open(filepath, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()

    def close(self):
        self.log_file.close()


def resolve_default_cookie() -> str | None:
    candidate_paths = [
        os.path.join(PROJECT_ROOT, "cookies.txt"),
        os.path.join(PROJECT_ROOT, "config", "cookies.txt"),
        os.path.join(os.getcwd(), "cookies.txt"),
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            return path
    return None


class EnhancedUserTester:
    def __init__(
        self,
        username: str,
        cookie_path: str | None = None,
        scope: str = "all",
        limit: int = 0,
        lang: str = "en",
    ):
        self.username = username.lstrip("@").strip()
        self.cookie_path = cookie_path or resolve_default_cookie()
        self.scope = scope.lower()
        self.limit = limit
        self.lang = "th" if lang.lower() == "th" else "en"
        self.t = I18N[self.lang]
        self.results = defaultdict(list)
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.worker: InspectionWorker | None = None

    def build_targets(self) -> list[dict]:
        targets = []
        if self.scope in ("all", "stories", "story"):
            targets.append(
                {
                    "url": f"https://www.instagram.com/stories/{self.username}/",
                    "scope": "all",
                }
            )
        if self.scope in ("all", "reels", "reel"):
            targets.append(
                {
                    "url": f"https://www.instagram.com/{self.username}/reels/",
                    "scope": "all",
                }
            )
        if self.scope in ("all", "posts", "feed"):
            targets.append(
                {"url": f"https://www.instagram.com/{self.username}/", "scope": "all"}
            )
        return targets

    def _translate_status(self, msg: str) -> str:
        if self.lang == "en":
            return msg

        clean = msg.strip()

        # Step headers
        if "Inspecting [" in clean:
            m = re.search(r"Inspecting \[(\d+)/(\d+)\]:\s*(\S+)", clean)
            if m:
                cur, total, target_id = m.group(1), m.group(2), m.group(3)
                if "stories" in target_id:
                    name = "สตอรี่ (Stories)"
                elif "reels" in target_id:
                    name = "คลิปรีลส์ (Reels)"
                else:
                    name = "หน้าฟีดและรูปภาพ (Posts)"
                return f"\n[*] กำลังตรวจสอบขั้นตอน [{cur}/{total}]: {name} ของ @{self.username}"

        # Initial Loaders
        if "Loading Feed from @" in clean:
            return f"[*] กำลังเชื่อมต่อไปยังหน้า Feed ของ @{self.username}..."
        if "Connecting to @" in clean:
            return f"[*] กำลังเชื่อมต่อไปยังหน้า Reels ของ @{self.username}..."
        if "Fetching active stories" in clean:
            return f"[*] กำลังค้นหา Stories ที่ยังไม่หมดอายุจาก @{self.username}..."

        # Batch & Pagination
        if "Fetching Feed page" in clean:
            m = re.search(r"Fetching Feed page (\d+)", clean)
            page = m.group(1) if m else "?"
            return f"[*] กำลังโหลด Feed หน้า {page} จาก @{self.username}..."
        if "Fetching Reels batch" in clean:
            m = re.search(r"Fetching Reels batch (\d+)", clean)
            batch = m.group(1) if m else "?"
            return f"[*] กำลังดึง Reels ชุดที่ {batch} จาก @{self.username}..."

        # Item counters
        if "Found [" in clean:
            m = re.search(
                r"Found \[(\d+)\]\s*(stories|reels|items)", clean, re.IGNORECASE
            )
            if m:
                count, category = m.group(1), m.group(2).lower()
                cat_name = (
                    "สตอรี่"
                    if category == "stories"
                    else ("รีลส์" if category == "reels" else "โพสต์")
                )
                return f"[*] พบ{cat_name}รายการที่ [{count}] จาก @{self.username}"

        return msg

    def run(self, save_json: bool = True):
        log_dir = os.path.join(PROJECT_ROOT, "logs", "live_tests")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_path = os.path.join(log_dir, f"live_{self.username}_{timestamp}.log")
        json_path = os.path.join(log_dir, f"payload_{self.username}_{timestamp}.json")

        logger = DualLogger(log_path)
        sys.stdout = logger

        try:
            targets = self.build_targets()
            cookie_desc = (
                self.t["attached"].format(path=self.cookie_path)
                if self.cookie_path
                else self.t["none"]
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

            self.worker = InspectionWorker(
                targets=targets, cookie_path=self.cookie_path, existing_shortcodes=set()
            )

            self.worker.progress_status.connect(self._on_status)
            self.worker.item_inspected.connect(self._on_item)
            self.worker.finished_inspection.connect(self._on_finished)

            self.worker.start()
            while self.worker.isRunning():
                app.processEvents()

            self._print_report(json_path if save_json else None)

        finally:
            sys.stdout = logger.terminal
            logger.close()

    def _on_status(self, msg: str):
        translated = self._translate_status(msg)
        print(translated)
        if "⚠️" in msg:
            self.warnings.append(msg.replace("[*]", "").strip())
        elif "Error" in msg or "401" in msg or "403" in msg:
            self.errors.append(msg.replace("[*]", "").strip())

    def _on_item(self, item: dict):
        mtype = item.get("media_type", "unknown")
        if mtype == "video" and len(item.get("raw_media_items", [])) == 0:
            warn_msg = (
                f"Video '{item.get('shortcode')}' has 0 CDN streams."
                if self.lang == "en"
                else f"วิดีโอ '{item.get('shortcode')}' ไม่มีสตรีม CDN ตรง"
            )
            self.warnings.append(warn_msg)
        self.results[mtype].append(item)

        total_found = sum(len(items) for items in self.results.values())
        if 0 < self.limit <= total_found and self.worker:
            print(self.t["limit_reached"].format(limit=self.limit))
            self.worker.cancel()

    def _on_finished(self, total: int):
        total_discovered = sum(len(items) for items in self.results.values())
        print(self.t["finished"].format(total=total_discovered))

    def _print_report(self, json_export_path: str | None = None):
        total_discovered = sum(len(items) for items in self.results.values())
        print("=======================================================")
        print(f" {self.t['summary_title'].format(username=self.username)}")
        print("=======================================================")
        print(f" {self.t['total_items']:<32}: {total_discovered}")
        print(f" {self.t['stories']:<32}: {len(self.results.get('story', []))}")
        print(f" {self.t['reels']:<32}: {len(self.results.get('video', []))}")
        print(f" {self.t['carousels']:<32}: {len(self.results.get('carousel', []))}")
        print(f" {self.t['photos']:<32}: {len(self.results.get('photo', []))}")
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
    parser.add_argument("-n", "--limit", type=int, default=0)
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
