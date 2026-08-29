"""
gui/styles.py - Instagram-inspired cute modern dark theme with tab segmented styling.
"""

from __future__ import annotations

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

MEDIA_TYPE_COLORS = {
    "REEL": {
        "bg": "rgba(131, 58, 180, 0.25)",
        "fg": "#DDA2F8",
        "border": "rgba(131, 58, 180, 0.5)",
    },
    "STORY": {
        "bg": "rgba(225, 48, 108, 0.25)",
        "fg": "#FF7597",
        "border": "rgba(225, 48, 108, 0.5)",
    },
    "HIGHLIGHT": {
        "bg": "rgba(252, 175, 69, 0.25)",
        "fg": "#FFCA7A",
        "border": "rgba(252, 175, 69, 0.5)",
    },
    "POST": {
        "bg": "rgba(0, 149, 246, 0.25)",
        "fg": "#70C5FF",
        "border": "rgba(0, 149, 246, 0.5)",
    },
    "CAROUSEL": {
        "bg": "rgba(245, 96, 64, 0.25)",
        "fg": "#FF9980",
        "border": "rgba(245, 96, 64, 0.5)",
    },
    "IMAGE": {
        "bg": "rgba(16, 185, 129, 0.25)",
        "fg": "#6EE7B7",
        "border": "rgba(16, 185, 129, 0.5)",
    },
    "VIDEO": {
        "bg": "rgba(131, 58, 180, 0.25)",
        "fg": "#DDA2F8",
        "border": "rgba(131, 58, 180, 0.5)",
    },
    "AUDIO": {
        "bg": "rgba(6, 182, 212, 0.25)",
        "fg": "#67E8F9",
        "border": "rgba(6, 182, 212, 0.5)",
    },
    "PROFILE": {
        "bg": "rgba(16, 185, 129, 0.25)",
        "fg": "#6EE7B7",
        "border": "rgba(16, 185, 129, 0.5)",
    },
    "LINK": {
        "bg": "rgba(110, 110, 130, 0.25)",
        "fg": "#C4C4D4",
        "border": "rgba(110, 110, 130, 0.5)",
    },
}

DARK_STYLESHEET = """
QMainWindow {
    background-color: #0D0D12;
}

QWidget {
    color: #FFFFFF;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    font-size: 9pt;
}

/* Glass Bento Enclosures */
QFrame#BentoPanel {
    background-color: #16161F;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
}

/* Modern Segmented Tab Bar */
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    background-color: #16161F;
    top: -1px;
}

QTabBar::tab {
    background-color: #16161F;
    color: #A0A0B2;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    padding: 7px 18px;
    margin-right: 4px;
    font-weight: 600;
    font-size: 8.5pt;
}

QTabBar::tab:hover {
    background-color: #1E1E2B;
    color: #FFFFFF;
}

QTabBar::tab:selected {
    background-color: #1E1E2B;
    color: #FFFFFF;
    border: 1px solid rgba(225, 48, 108, 0.5);
    border-bottom: 2px solid #E1306C;
}

/* Cute Pill Buttons */
QPushButton {
    background-color: #1E1E2A;
    color: #F0F0F5;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 5px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #272738;
    border: 1px solid rgba(225, 48, 108, 0.4);
    color: #FFFFFF;
}

QPushButton:pressed {
    background-color: #191924;
}

/* Instagram Gradient Primary Buttons */
QPushButton#PrimaryActionButton, QPushButton#DownloadAllButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #833AB4, stop:0.5 #E1306C, stop:1 #F56040);
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    font-weight: 700;
    padding: 6px 18px;
}

QPushButton#PrimaryActionButton:hover, QPushButton#DownloadAllButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9546CD, stop:0.5 #EE3E7A, stop:1 #F77254);
}

QPushButton#PrimaryActionButton:pressed, QPushButton#DownloadAllButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #702F9C, stop:0.5 #C7255B, stop:1 #D94D2E);
}

QPushButton#DestructiveButton {
    background-color: rgba(239, 68, 68, 0.12);
    color: #FF6B6B;
    border: 1px solid rgba(239, 68, 68, 0.25);
}

QPushButton#DestructiveButton:hover {
    background-color: rgba(239, 68, 68, 0.24);
    border: 1px solid rgba(239, 68, 68, 0.45);
    color: #FF8787;
}

/* Cute Glowing Checkbox */
QCheckBox {
    color: #A0A0B2;
    font-weight: 500;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1.5px solid rgba(255, 255, 255, 0.22);
    background-color: #1A1A26;
}

QCheckBox::indicator:hover {
    border-color: #E1306C;
}

QCheckBox::indicator:checked {
    background-color: #E1306C;
    border-color: #E1306C;
}

/* Smooth Minimal Scrollbar */
QScrollArea {
    background: transparent;
    border: none;
}

QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 6px;
    margin: 4px 2px;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.16);
    min-height: 24px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background: #E1306C;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""
