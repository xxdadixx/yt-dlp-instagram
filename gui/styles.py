"""
gui/styles.py - Studio Liquid Glass Design System for Instagram Pro Studio.
Features neutral obsidian glass layering, 1px specular micro-bevels,
tactile vector checkmarks, and focused Instagram sunset CTA accents.
"""

from __future__ import annotations

DARK_STYLESHEET = """
/* =========================================================================
   1. Window Canvas & Obsidian Glass Backdrop
   ========================================================================= */
QMainWindow, QWidget#centralWidget {
    background-color: #0B0B0F;
    color: #F8FAFC;
    font-family: -apple-system, 'SF Pro Display', 'Segoe UI Variable Display', 'Segoe UI', Roboto, sans-serif;
    font-size: 13px;
    letter-spacing: 0.15px;
}

QScrollArea {
    background: transparent;
    border: none;
}

QScrollArea > QWidget > QWidget {
    background: transparent;
}

/* =========================================================================
   2. Segmented Glass Tabs & Navigators
   ========================================================================= */
QTabWidget::pane {
    background: rgba(19, 19, 26, 0.70);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-top: 1.5px solid rgba(255, 255, 255, 0.14);
    border-radius: 14px;
    margin-top: -1px;
}

QTabBar::tab {
    background: rgba(255, 255, 255, 0.02);
    color: #8E8EA0;
    padding: 9px 22px;
    margin-right: 4px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    border: 1px solid transparent;
    font-size: 12.5px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 rgba(225, 48, 108, 0.18),
        stop: 0.35 rgba(255, 255, 255, 0.04),
        stop: 1 rgba(19, 19, 26, 0.85)
    );
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-top: 2px solid #E1306C;
    border-bottom: 1px solid transparent;
}

QTabBar::tab:hover:!selected {
    background: rgba(255, 255, 255, 0.04);
    color: #CBD5E1;
}

/* =========================================================================
   3. Frosted Text Input Fields
   ========================================================================= */
QLineEdit {
    background: rgba(22, 22, 30, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-top: 1.2px solid rgba(255, 255, 255, 0.16);
    border-radius: 10px;
    padding: 8px 16px;
    color: #FFFFFF;
    font-size: 13px;
    selection-background-color: #E1306C;
    selection-color: #FFFFFF;
}

QLineEdit:hover {
    border: 1px solid rgba(255, 255, 255, 0.18);
    background: rgba(26, 26, 36, 0.85);
}

QLineEdit:focus {
    border: 1.5px solid #E1306C;
    border-top: 1.5px solid #FF7597;
    background: rgba(28, 28, 38, 0.95);
}

/* =========================================================================
   4. Liquid Glass Dropdowns (QComboBox)
   ========================================================================= */
QComboBox {
    background: rgba(22, 22, 30, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-top: 1.2px solid rgba(255, 255, 255, 0.16);
    border-radius: 9px;
    padding: 6px 32px 6px 14px;
    color: #CBD5E1;
    font-size: 12.5px;
    font-weight: 600;
}

QComboBox:hover {
    background: rgba(28, 28, 38, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.20);
    color: #FFFFFF;
}

QComboBox:on, QComboBox:focus {
    background: rgba(30, 30, 42, 0.95);
    border: 1.5px solid #E1306C;
    color: #FFFFFF;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: 1px solid rgba(255, 255, 255, 0.06);
    background: transparent;
}

QComboBox::down-arrow {
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238E8EA0' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
    width: 11px;
    height: 11px;
}

QComboBox::down-arrow:hover, QComboBox::down-arrow:on {
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23E1306C' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>");
}

QComboBox QAbstractItemView {
    background-color: #161620;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    padding: 5px;
    outline: none;
    selection-background-color: transparent;
}

QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 5px 12px;
    margin: 2px;
    border-radius: 6px;
    color: #CBD5E1;
    font-weight: 500;
}

QComboBox QAbstractItemView::item:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #FFFFFF;
}

QComboBox QAbstractItemView::item:selected {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #833AB4,
        stop: 0.5 #E1306C,
        stop: 1 #FCAF45
    );
    color: #FFFFFF;
    font-weight: 700;
}

/* =========================================================================
   5. Liquid Buttons Suite
   ========================================================================= */
QPushButton {
    border-radius: 9px;
    padding: 7px 16px;
    font-size: 13px;
    font-weight: 600;
    outline: none;
}

/* Primary Hero Action Buttons (Inspect, Download) */
QPushButton#PrimaryActionButton,
QPushButton#DownloadAllButton {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #833AB4,
        stop: 0.35 #C13584,
        stop: 0.70 #E1306C,
        stop: 0.88 #FD1D1D,
        stop: 1 #FCAF45
    );
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-top: 1.5px solid rgba(255, 255, 255, 0.65);
    border-bottom: 1.5px solid rgba(0, 0, 0, 0.40);
    border-radius: 10px;
    padding: 0px;
    font-weight: 700;
}

QPushButton#PrimaryActionButton:hover,
QPushButton#DownloadAllButton:hover {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #9546CD,
        stop: 0.35 #D64296,
        stop: 0.70 #FF4580,
        stop: 0.88 #FF334B,
        stop: 1 #FFC062
    );
    border: 1.2px solid #FFFFFF;
}

QPushButton#PrimaryActionButton:pressed,
QPushButton#DownloadAllButton:pressed {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #6C2B97,
        stop: 0.35 #9A2466,
        stop: 0.70 #BA1F52,
        stop: 0.88 #C71228,
        stop: 1 #D4892A
    );
    border-top: 1.5px solid rgba(0, 0, 0, 0.30);
    padding-top: 1px;
}

QPushButton#PrimaryActionButton:disabled,
QPushButton#DownloadAllButton:disabled {
    background: rgba(30, 30, 40, 0.45) !important;
    color: #555566 !important;
    border: 1px solid rgba(255, 255, 255, 0.04) !important;
}

/* Frosted Acrylic Secondary Buttons */
QPushButton#GlassActionButton {
    background: rgba(255, 255, 255, 0.035);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-top: 1.2px solid rgba(255, 255, 255, 0.18);
    border-radius: 9px;
    color: #CBD5E1;
}

QPushButton#GlassActionButton:hover {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(225, 48, 108, 0.65);
    border-top: 1.5px solid rgba(255, 255, 255, 0.35);
    color: #FFFFFF;
}

QPushButton#GlassActionButton:pressed {
    background: rgba(225, 48, 108, 0.12);
    padding-top: 1px;
}

QPushButton#GlassActionButton:disabled {
    background-color: rgba(255, 255, 255, 0.015) !important;
    color: #4A4A5A !important;
    border: 1px solid rgba(255, 255, 255, 0.03) !important;
}

/* Destructive Ruby Glass Buttons */
QPushButton#DestructiveButton {
    background: rgba(239, 68, 68, 0.10);
    color: #F87171;
    border: 1px solid rgba(239, 68, 68, 0.25);
    border-top: 1.2px solid rgba(254, 202, 202, 0.30);
    border-radius: 9px;
    font-weight: 600;
}

QPushButton#DestructiveButton:hover {
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0, stop: 0 #EF4444, stop: 1 #DC2626);
    border: 1px solid #FCA5A5;
    color: #FFFFFF;
}

QPushButton#DestructiveButton:pressed {
    background: #991B1B;
    padding-top: 1px;
}

QPushButton#DestructiveButton:disabled {
    background-color: rgba(239, 68, 68, 0.02) !important;
    color: #4A3338 !important;
    border: 1px solid rgba(239, 68, 68, 0.05) !important;
}

/* =========================================================================
   6. Authentic Vector Glass Checkboxes
   ========================================================================= */
QCheckBox {
    color: #94A3B8;
    spacing: 7px;
    font-size: 12.5px;
    font-weight: 500;
}

QCheckBox:hover {
    color: #F1F5F9;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4.5px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-top: 1.2px solid rgba(255, 255, 255, 0.28);
}

QCheckBox::indicator:hover {
    border: 1px solid rgba(225, 48, 108, 0.70);
    background: rgba(225, 48, 108, 0.08);
}

/* Inlined clean vector SVG checkmark on checked state */
QCheckBox::indicator:checked {
    background: #E1306C;
    border: 1px solid #FF7597;
    border-top: 1.2px solid #FFA6BC;
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'/></svg>");
}

/* =========================================================================
   7. Glass Tooltips & Popups
   ========================================================================= */
QToolTip {
    background-color: #171722;
    color: #F8FAFC;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-top: 1.2px solid rgba(225, 48, 108, 0.65);
    border-radius: 7px;
    padding: 5px 9px;
    font-size: 11.5px;
}

/* =========================================================================
   8. Minimalist Refraction Scrollbars
   ========================================================================= */
QScrollBar:vertical {
    border: none;
    background: rgba(0, 0, 0, 0.15);
    width: 7px;
    border-radius: 3.5px;
    margin: 4px 2px 4px 0px;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.12);
    min-height: 36px;
    border-radius: 3.5px;
}

QScrollBar::handle:vertical:hover {
    background: #E1306C;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0px;
    background: transparent;
}
"""

MEDIA_TYPE_COLORS = {
    "POST": {
        "bg": "rgba(56, 189, 248, 0.12)",
        "fg": "#38BDF8",
        "border": "rgba(56, 189, 248, 0.32)",
    },
    "REEL": {
        "bg": "rgba(225, 48, 108, 0.14)",
        "fg": "#FF6B8B",
        "border": "rgba(225, 48, 108, 0.38)",
    },
    "CAROUSEL": {
        "bg": "rgba(245, 96, 64, 0.14)",
        "fg": "#F56040",
        "border": "rgba(245, 96, 64, 0.35)",
    },
    "STORY": {
        "bg": "rgba(168, 85, 247, 0.14)",
        "fg": "#C084FC",
        "border": "rgba(168, 85, 247, 0.35)",
    },
    "PHOTO": {
        "bg": "rgba(56, 189, 248, 0.12)",
        "fg": "#38BDF8",
        "border": "rgba(56, 189, 248, 0.32)",
    },
    "VIDEO": {
        "bg": "rgba(168, 85, 247, 0.14)",
        "fg": "#A855F7",
        "border": "rgba(168, 85, 247, 0.35)",
    },
}

COLORS = {
    "background": "#0B0B0F",
    "surface": "#13131A",
    "surface_secondary": "#1A1A24",
    "surface_glass": "rgba(22, 22, 30, 0.70)",
    "card": "#181822",
    "card_hover": "#20202E",
    "border": "rgba(255, 255, 255, 0.08)",
    "border_specular": "rgba(255, 255, 255, 0.18)",
    "border_focus": "#E1306C",
    "text_primary": "#FFFFFF",
    "text_secondary": "#CBD5E1",
    "text_muted": "#8E8EA0",
    "ig_purple": "#833AB4",
    "ig_magenta": "#C13584",
    "ig_pink": "#E1306C",
    "ig_crimson": "#FD1D1D",
    "ig_orange": "#F56040",
    "ig_amber": "#FCAF45",
}
