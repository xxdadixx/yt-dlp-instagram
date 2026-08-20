"""
gui/styles.py - Dark theme, modern scrollbars, and card stylesheet definitions.
"""

DARK_THEME_QSS = """
QWidget {
    background-color: #16161c;
    color: #eaeaea;
    font-family: 'Segoe UI', sans-serif;
    font-size: 9pt;
}

QGroupBox {
    border: 1px solid #2e2e3d;
    border-radius: 6px;
    margin-top: 8px;
    font-weight: bold;
    color: #fa7e1e;
    padding: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

QPlainTextEdit {
    background-color: #20202a;
    border: 1px solid #38384a;
    border-radius: 5px;
    padding: 5px;
    color: #ffffff;
}

QPushButton {
    background-color: #d62976;
    border: none;
    border-radius: 5px;
    color: #ffffff;
    font-weight: bold;
    padding: 5px 12px;
}
QPushButton:hover { background-color: #fa7e1e; }
QPushButton:disabled { background-color: #2c2c38; color: #606070; }

QProgressBar {
    background-color: #20202a;
    border: 1px solid #38384a;
    border-radius: 4px;
    text-align: center;
    color: #ffffff;
    font-size: 9px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #fa7e1e, stop:1 #d62976);
    border-radius: 3px;
}

QCheckBox { color: #cccccc; }

/* ========================================================================= */
/* Modern Minimalist ScrollBar Styling                                       */
/* ========================================================================= */
QScrollArea {
    background-color: transparent;
    border: none;
}

/* Vertical ScrollBar */
QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
    margin: 4px 2px 4px 0px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background-color: #2e2e3e;
    min-height: 35px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background-color: #4a4a62;
}
QScrollBar::handle:vertical:pressed {
    background-color: #d62976;
}
QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {
    height: 0px;
    width: 0px;
    background: transparent;
    border: none;
}
QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* Horizontal ScrollBar */
QScrollBar:horizontal {
    background-color: transparent;
    height: 8px;
    margin: 0px 4px 2px 4px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background-color: #2e2e3e;
    min-width: 35px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #4a4a62;
}
QScrollBar::handle:horizontal:pressed {
    background-color: #d62976;
}
QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {
    height: 0px;
    width: 0px;
    background: transparent;
    border: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
"""

CARD_SELECTED_QSS = """
MediaCardWidget {
    background-color: #2b1f2d;
    border: 2px solid #d62976;
    border-radius: 8px;
}
QLabel { color: #ffffff; }
QComboBox {
    background-color: #17171e;
    border: 1px solid #d62976;
    border-radius: 4px;
    padding: 4px 8px;
    color: #ffffff;
    font-size: 11px;
}
"""

CARD_DEFAULT_QSS = """
MediaCardWidget {
    background-color: #21212b;
    border: 1px solid #363647;
    border-radius: 8px;
}
MediaCardWidget:hover {
    border: 1px solid #5a5a72;
}
QLabel { color: #eaeaea; }
QComboBox {
    background-color: #17171e;
    border: 1px solid #4a4a5e;
    border-radius: 4px;
    padding: 4px 8px;
    color: #ffffff;
    font-size: 11px;
}
"""
