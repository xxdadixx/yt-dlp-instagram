import json
import glob
import os

# ค้นหาไฟล์ Payload ล่าสุด
files = sorted(glob.glob("logs/live_tests/payload_niun_iuo_*.json"))
if not files:
    print("ไม่พบไฟล์ JSON ในโฟลเดอร์ logs/live_tests/")
    exit()

latest_file = files[-1]
with open(latest_file, "r", encoding="utf-8") as f:
    items = json.load(f)

print(f"กำลังตรวจสอบไฟล์: {os.path.basename(latest_file)}")
print("-------------------------------------------------------")

# 1. นับแยกประเภทของสื่อ
stories = [i for i in items if i.get("media_type") == "story"]
reels = [i for i in items if i.get("media_type") == "video"]
carousels = [i for i in items if i.get("media_type") == "carousel"]
photos = [i for i in items if i.get("media_type") == "photo"]

print(f"• สตอรี่ 24 ชม. (Stories)      : {len(stories)} รายการ")
print(f"• คลิปรีลส์ (Reels)            : {len(reels)} รายการ")
print(f"• อัลบั้ม (Carousels)         : {len(carousels)} รายการ")
print(f"• รูปภาพเดี่ยว (Photos)        : {len(photos)} รายการ")
print(f"• รวมโพสต์ถาวร (Timeline Media): {len(reels) + len(carousels) + len(photos)} รายการ")
print("-------------------------------------------------------")

# 2. ตัวอย่าง Shortcode ล่าสุด 5 รายการแรก
print("ตัวอย่าง Shortcode ที่ดึงมาได้:")
for i, item in enumerate(items[:5], 1):
    print(f" [{i}] Type: {item.get('media_type'):<8} | Code: {item.get('shortcode')} | URL: {item.get('url')}")