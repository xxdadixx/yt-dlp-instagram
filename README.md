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