# Instagram Pro Downloader - Studio Inspector

A high-performance, studio-grade desktop application built with **Python**, **PyQt6**, and **yt-dlp** for inspecting, queuing, and batch-downloading Instagram media (Posts, Reels, Carousels, Stories, and full Profile Feeds).

---

## Key Features

- **Multi-Tier Fallback Pipeline:** Triple-engine resolution via Instagram Authenticated Web API, Mobile REST API, and `yt-dlp` scraping fallback.
- **Full Profile & Reels Scraping:** Automated cursor-based pagination (`max_id`) to extract an entire profile's Feed or Reels archive.
- **Scope Filter:** Selective profile extraction modes (`🌟 All Media`, `🎬 Videos & Reels Only`, `🖼️ Photos Only`).
- **Direct CDN Chunk Streaming:** Fast multi-threaded chunked downloads (64 KB/chunk) bypassing bot detection.
- **Audio Extraction:** Integrated local FFmpeg post-processing to extract MP3 audio (192 kbps) directly from Reels and Video Posts.
- **Interactive Studio Workspace:**
  - Modern Dark QSS Theme with GPU-friendly entry fade-in animations.
  - Live thumbnail previews with anti-hotlinking headers.
  - Real-time internationalization (English / ภาษาไทย).
  - Clipboard Auto-Monitor for instant link detection.
  - Duplicate detection to prevent re-inspecting or re-downloading completed items.

---

## Project Structure

```text
yt-dlp-instagram/
├── config/
│   ├── constants.py         # Global parameters, User-Agents & Regex patterns
│   └── translations.py      # i18n dictionary (TH / EN)
├── core/
│   ├── cookie_manager.py    # Netscape cookies.txt parser & OpenerDirector
│   ├── download_worker.py   # Multi-threaded CDN streaming & FFmpeg pipeline
│   ├── inspect_worker.py    # Background media inspector & profile pagination
│   └── parser.py            # Instagram URL decomposition & ID conversion
├── gui/
│   ├── icons.py             # Vector SVG rendering helper
│   ├── main_window.py       # Main studio layout & event orchestration
│   ├── styles.py            # Dark theme QSS stylesheets
│   └── widgets/
│       ├── media_card.py    # Dynamic interactive media card
│       ├── modern_progress_bar.py # Lerp-interpolated shimmer progress bar
│       ├── no_scroll_combo.py     # Wheel-isolated combo box
│       ├── thumbnail_loader.py    # Thread-safe async image loader
│       └── url_chip_input.py      # URL chip deck with scope dropdowns
├── utils/
│   ├── file_utils.py        # Path resolution for binaries & assets
│   └── logger.py            # Silent worker logger
├── main.py                  # Application entry point
└── requirements.txt         # Project dependencies

---

## Installation & Setup
- **Prerequisites**
  -  Python 3.10 or higher
  -  FFmpeg (placed in system PATH or root folder)

---

## Setup Environment

```powershell
git clone [https://github.com/xxdadixx/yt-dlp-instagram.git](https://github.com/xxdadixx/yt-dlp-instagram.git)
cd yt-dlp-instagram

# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

---

## Run Application

```powershell
python main.py

---

## Authentication (**cookies.txt**)

To inspect private accounts you follow, view active 24-hour Stories, or bypass Instagram scraping limits

- Export cookies from your logged-in Instagram browser session using an extension (e.g., Get cookies.txt LOCALLY).
- Click Import Cookie in the application's bottom deck to load your **cookies.txt**

---

## License & Disclaimer

This software is developed for educational and personal archival purposes only. Please adhere to Instagram's Terms of Service when downloading media.

---

### How to Apply and Push the Update

1. Replace the entire content of your local `README.md` file with the block above.
2. In your terminal, run:

```powershell
git add README.md
git commit -m "docs: complete readme with setup and repository urls"
git push origin main