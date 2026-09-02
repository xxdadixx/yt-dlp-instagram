# Instagram Pro Downloader - Studio Inspector

A high-performance, studio-grade desktop application built with **Python**, **PyQt6**, and **yt-dlp** for inspecting, queuing, and batch-downloading Instagram media (Posts, Reels, Carousels, Stories, and full Profile Feeds).

---

## Key Features

- **Multi-Tier Fallback Pipeline:** Triple-engine resolution cascading across Instagram Web GraphQL Persisted Queries, native Web Profile Feed API, captioned Embed scraping, and `yt-dlp` scraping fallback.
- **Deep Cursor Pagination:** Automated cursor traversal (`max_id` / `end_cursor`) to extract an entire profile's Grid or Reels archive with selectable crawl budget limits (36, 72, 120, 240, 480, 960, or Unlimited).
- **Anti-Scraping Safeguards:** Adaptive Gaussian request pacing, multi-tiered macro dwell cooldowns, unauthenticated-first lookups, and circuit-breaker tripwires on checkpoints or HTTP 429 rate limits.
- **Direct CDN Streaming:** Fast 256 KB chunked streaming with `urllib3` connection pooling (16 concurrent adapters), monotonic chronological sorting (Most Recent -> Oldest), and atomic file replacement (`.part` staging).
- **Audio Extraction:** Integrated FFmpeg post-processing to extract MP3 audio (192 kbps) directly from Reels and Video Posts via quality preset options.
- **Studio Workspace:**
  - Liquid Glass dark theme with GPU-accelerated QSS styling and cursor-reactive specular lighting.
  - Interactive photo gallery lightbox dialog with preloaded slide swiping.
  - Real-time activity log viewer with ring-buffered memory protection and disk export.
  - Automatic clipboard monitor for auto-pasting copied Instagram URLs.
  - Multi-language interface with dynamic English and ภาษาไทย (Thai) translation switching.

---

## Project Structure

```text
yt-dlp-instagram/
├── config/
│   ├── constants.py               # Global parameters, User-Agents, and regex patterns
│   ├── instagram_cookies.txt      # Sanitized template Netscape cookie file
│   ├── settings.json              # Local workspace preferences and geometry
│   └── translations.py            # Bilingual i18n dictionary (EN / TH)
├── core/
│   ├── client_engine.py           # Resilient HTTP/2 session and persisted GraphQL client
│   ├── cookie_manager.py          # Netscape cookie importer, parser, and AppData persistence
│   ├── download_worker.py         # Chronological 256 KB CDN chunk streaming and FFmpeg worker
│   ├── inspect_worker.py          # Multi-tier background media inspector and profile crawler
│   └── parser.py                  # Instagram URL normalizer and GraphQL node parser
├── gui/
│   ├── icons.py                   # High-DPI vector SVG rendering engine
│   ├── main_window.py             # Studio workspace orchestration and event handling
│   ├── styles.py                  # Frosted acrylic and specular QSS design system
│   └── widgets/
│       ├── image_viewer_dialog.py # High-resolution carousel lightbox gallery dialog
│       ├── log_viewer_widget.py   # Real-time process diagnostic viewer with log export
│       ├── media_card.py          # Media item card container with dynamic thumbnail caching
│       ├── modern_progress_bar.py # Shimmer-animated progress bar with OutCubic tweening
│       ├── no_scroll_combo.py     # Wheel-isolated combo box
│       ├── thumbnail_loader.py    # Thread-safe LRU thumbnail memory cache and QThreadPool loader
│       └── url_chip_input.py      # Multi-target URL chip deck with format badges
├── tests/
│   ├── live_user_test.py          # Standalone bilingual CLI diagnostic tool for live user crawls
│   ├── test_audit_suite.py        # Unit test suite for thread safety, parser, and UI queues
│   └── test_safeguards.py         # Unit test suite for circuit breakers and pacing bounds
├── utils/
│   ├── file_utils.py              # OS AppData directories, FFmpeg locator, and filename sanitizer
│   └── logger.py                  # Thread-safe Qt log emission bridge
├── InstagramProDownloader.spec    # PyInstaller multi-module bundle specification
├── main.py                        # Application entry point and per-monitor DPI bootstrapping
├── requirements.txt               # Production and build dependencies
├── run_tests.py                   # Automated discovery test runner and report logger
└── setup.iss                      # Inno Setup Windows installer compiler script
```

---

## Installation & Setup

### 1. Prerequisites
- **Python 3.10** or higher
- **FFmpeg** (installed on system `PATH` or placed inside the project folder)

### 2. Environment Setup
```bash
git clone [https://github.com/xxdadixx/yt-dlp-instagram.git](https://github.com/xxdadixx/yt-dlp-instagram.git)
cd yt-dlp-instagram

# Create and activate virtual environment
python -m venv venv

# Windows:
.\venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Application
```bash
python main.py
```

---

## Authentication (`cookies.txt`)

Public Instagram endpoints limit unauthenticated crawling to initial grid items. To crawl complete Reels feeds, inspect private profiles you follow, or view active Stories:
1. Export cookies from your logged-in browser session using a browser extension (such as *Get cookies.txt LOCALLY*) in Netscape format.
2. In the application header bar, click the **Cookie (Key)** button and select your exported cookie file.
3. Cookies are automatically validated, normalized, and saved to your protected OS application data directory (`%APPDATA%\InstagramProDownloader\instagram_cookies.txt` on Windows).

---

## Testing & Diagnostics

### Run Automated Unit Test Suite
Executes all regression tests for thread safety, circuit breakers, parser rules, and UI queues, outputting a summary report to `logs/`:
```bash
python run_tests.py
```

### Run Live CLI User Scrape Diagnostic
Diagnoses live Instagram API responses and verifies endpoint connectivity for a specific account:
```bash
# General profile crawl
python tests/live_user_test.py <username> --scope all --limit 36

# Reels-only crawl in Thai language
python tests/live_user_test.py <username> --scope reels --limit 72 --lang th
```

---

## Packaging & Distribution

### 1. Build Standalone Executable (PyInstaller)
Compile the application into a standalone Windows folder bundle:
```bash
pyinstaller InstagramProDownloader.spec
```
The output binary will be located in `dist/InstagramProDownloader/InstagramProDownloader.exe`.

### 2. Build Windows Setup Installer (Inno Setup)
Open `setup.iss` in **Inno Setup Compiler** and run compile (or use the command line compiler):
```bash
iscc setup.iss
```
The production setup installer will be generated in `installer_output/InstagramProDownloader_Setup.exe`.

---

## License & Disclaimer

This software is developed for personal archival and educational purposes only. Please review and adhere to Instagram's Terms of Service when inspecting and downloading content.