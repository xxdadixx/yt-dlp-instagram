"""
gui/styles.py - Liquid Glass Design System for Instagram Pro Studio.
Features frosted acrylic layers, specular edge gradients, and Apple-inspired dark mode surfaces.
"""

from __future__ import annotations

DARK_STYLESHEET = """
/* =========================================================================
   1. Window Canvas & Acrylic Backdrop
   ========================================================================= */
QMainWindow, QWidget#centralWidget {
    background-color: #0D0C13;
    color: #F8FAFC;
    font-family: -apple-system, 'SF Pro Display', 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
}

/* =========================================================================
   2. Segmented Glass Tabs & Navigators
   ========================================================================= */
QTabWidget::pane {
    background: rgba(22, 20, 32, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    margin-top: -1px;
}

QTabBar::tab {
    background: rgba(255, 255, 255, 0.03);
    color: #94A3B8;
    padding: 8px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid transparent;
    font-size: 11px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255, 255, 255, 0.12), stop:1 rgba(255, 255, 255, 0.04));
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-bottom: 1px solid transparent;
}

QTabBar::tab:hover:!selected {
    background: rgba(255, 255, 255, 0.06);
    color: #CBD5E1;
}

/* =========================================================================
   3. Frosted Input Fields & Combos
   ========================================================================= */
QLineEdit {
    background-color: rgba(24, 22, 35, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 7px 14px;
    color: #FFFFFF;
    font-size: 12px;
    selection-background-color: #E1306C;
}

QLineEdit:focus {
    border: 1.5px solid #E1306C;
    background-color: rgba(30, 27, 44, 0.85);
}

QComboBox {
    background-color: rgba(24, 22, 35, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 8px;
    padding: 4px 12px;
    color: #E2E8F0;
    font-size: 11px;
    font-weight: 600;
}

QComboBox:hover {
    border: 1px solid rgba(255, 255, 255, 0.22);
    background-color: rgba(32, 29, 48, 0.75);
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #171522;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    selection-background-color: #E1306C;
    selection-color: #FFFFFF;
    padding: 4px;
    outline: none;
}

/* =========================================================================
   4. Liquid Action Buttons Suite
   ========================================================================= */
QPushButton {
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 11.5px;
    font-weight: 600;
    outline: none;
}

/* Primary Sunset Glow (Inspect & Download) */
QPushButton#PrimaryActionButton,
QPushButton#DownloadAllButton {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #833AB4,
        stop: 0.5 #FD1D1D,
        stop: 1 #FCB045
    );
    color: #FFFFFF;
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 10px;
    padding: 8px 20px;
    font-weight: 700;
}

QPushButton#PrimaryActionButton:hover,
QPushButton#DownloadAllButton:hover {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #9546CD,
        stop: 0.5 #FF334B,
        stop: 1 #FFC062
    );
    border: 1.5px solid #FFFFFF;
}

QPushButton#PrimaryActionButton:pressed,
QPushButton#DownloadAllButton:pressed {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #6C2B97,
        stop: 0.5 #C71228,
        stop: 1 #D4892A
    );
    padding-top: 9px;
    padding-bottom: 7px;
}

QPushButton#PrimaryActionButton:disabled,
QPushButton#DownloadAllButton:disabled {
    background: rgba(30, 27, 40, 0.5) !important;
    color: #4A4A5A !important;
    border: 1px solid rgba(255, 255, 255, 0.04) !important;
}

/* Frosted Glass Action Buttons */
QPushButton#GlassActionButton {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 8px;
    color: #CBD5E1;
}

QPushButton#GlassActionButton:hover {
    background-color: rgba(255, 255, 255, 0.09);
    border: 1px solid rgba(225, 48, 108, 0.6);
    color: #FFFFFF;
}

QPushButton#GlassActionButton:pressed {
    background-color: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(225, 48, 108, 0.3);
    padding-top: 7px;
    padding-bottom: 5px;
}

QPushButton#GlassActionButton:disabled {
    background-color: rgba(255, 255, 255, 0.015) !important;
    color: #454552 !important;
    border: 1px solid rgba(255, 255, 255, 0.03) !important;
}

/* Destructive Crimson Bevel */
QPushButton#DestructiveButton {
    background-color: rgba(239, 68, 68, 0.12);
    color: #F87171;
    border: 1px solid rgba(239, 68, 68, 0.30);
    border-radius: 8px;
    font-weight: 600;
}

QPushButton#DestructiveButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #EF4444, stop:1 #DC2626);
    border: 1px solid #FCA5A5;
    color: #FFFFFF;
}

QPushButton#DestructiveButton:pressed {
    background: #991B1B;
    padding-top: 7px;
    padding-bottom: 5px;
}

QPushButton#DestructiveButton:disabled {
    background-color: rgba(239, 68, 68, 0.02) !important;
    color: #4A3338 !important;
    border: 1px solid rgba(239, 68, 68, 0.06) !important;
}

/* =========================================================================
   5. Smooth Glassmorphic Scrollbars
   ========================================================================= */
QScrollBar:vertical {
    border: none;
    background: rgba(0, 0, 0, 0.2);
    width: 6px;
    border-radius: 3px;
    margin: 4px 2px 4px 0px;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.15);
    min-height: 24px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(225, 48, 108, 0.6);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0px;
    background: transparent;
}
"""

MEDIA_TYPE_COLORS = {
    "POST": {
        "bg": "rgba(56, 189, 248, 0.15)",
        "fg": "#38BDF8",
        "border": "rgba(56, 189, 248, 0.35)",
    },
    "REEL": {
        "bg": "rgba(225, 48, 108, 0.18)",
        "fg": "#FF7597",
        "border": "rgba(225, 48, 108, 0.45)",
    },
    "CAROUSEL": {
        "bg": "rgba(245, 96, 64, 0.15)",
        "fg": "#F56040",
        "border": "rgba(245, 96, 64, 0.35)",
    },
    "STORY": {
        "bg": "rgba(168, 85, 247, 0.15)",
        "fg": "#C084FC",
        "border": "rgba(168, 85, 247, 0.35)",
    },
}

COLORS = {
    "background": "#0D0D12",
    "surface": "#16161F",
    "surface_secondary": "#1E1E2A",
    "card": "#212130",
    "card_hover": "#29293C",
    "border": "rgba(255, 255, 255, 0.08)",
    "border_focus": "#E1306C",
    "text_primary": "#FFFFFF",
    "text_secondary": "#A0A0B2",
    "text_muted": "#6E6E82",
    "ig_pink": "#E1306C",
    "ig_purple": "#833AB4",
    "ig_orange": "#F56040",
    "ig_yellow": "#FCAF45",
}
