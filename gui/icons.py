import os
import sys
from typing import Dict, Optional

try:
    from PyQt6.QtCore import Qt, QSize, QByteArray, QPointF, QRectF
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath

    try:
        from PyQt6.QtSvg import QSvgRenderer

        HAS_SVG = True
    except ImportError:
        HAS_SVG = False
except ImportError:
    HAS_SVG = False

    class QSize:
        def __init__(self, w=0, h=0):
            self.w, self.h = w, h

    class QIcon:
        def __init__(self, *a):
            pass

        def isNull(self):
            return False

    class QPixmap:
        def __init__(self, *a):
            pass

    class QColor:
        def __init__(self, *a):
            pass


SVG_ICONS: Dict[str, str] = {
    "search": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/>
        <line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>""",
    "clear": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
    </svg>""",
    "trash": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
    </svg>""",
    "select_all": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 11 12 14 22 4"/>
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
    </svg>""",
    "clear_completed": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 6L6 18"/>
        <path d="M6 6l12 12"/>
        <circle cx="12" cy="12" r="10"/>
    </svg>""",
    "check_double": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M7 12l5 5L22 7"/>
        <path d="M2 12l5 5L12 12"/>
    </svg>""",
    "folder": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>""",
    "folder_open": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v1H6.2a2 2 0 0 0-1.9 1.4L2 19.5V5z"/>
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2l2.3-9.2A2 2 0 0 1 6.2 8H22z"/>
    </svg>""",
    "key": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 2l-2 2m-1.5 1.5L14 9l-3 3 2 2 2-2 3-3 1.5-1.5z"/>
        <circle cx="7.5" cy="16.5" r="4.5"/>
    </svg>""",
    "cookie": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5"/>
        <path d="M8.5 8.5v.01"/>
        <path d="M11.5 15.5v.01"/>
        <path d="M15.5 11.5v.01"/>
        <path d="M8.5 14.5v.01"/>
    </svg>""",
    "download": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>""",
    "stop": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="4" width="16" height="16" rx="2" ry="2"/>
    </svg>""",
    "cancel": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="15" y1="9" x2="9" y2="15"/>
        <line x1="9" y1="9" x2="15" y2="15"/>
    </svg>""",
    "link": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
    </svg>""",
    "plus": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="5" x2="12" y2="19"/>
        <line x1="5" y1="12" x2="19" y2="12"/>
    </svg>""",
    "export": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>""",
}

_ICON_CACHE: Dict[str, QIcon] = {}


def get_icon(name: str, color: str = "#ffffff", size: int = 18) -> QIcon:
    """Returns a crisp, high-DPI QIcon from the SVG icon library with fallback support."""
    cache_key = f"{name}_{color}_{size}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    if "PyQt6.QtGui" not in sys.modules and "QPainter" not in globals():
        icon = QIcon()
        _ICON_CACHE[cache_key] = icon
        return icon

    try:
        svg_template = SVG_ICONS.get(name)
        if not svg_template:
            return QIcon()

        svg_content = svg_template.format(color=color)

        if HAS_SVG:
            renderer = QSvgRenderer(QByteArray(svg_content.encode("utf-8")))
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            icon = QIcon(pixmap)
            _ICON_CACHE[cache_key] = icon
            return icon
        else:
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(color))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            if name in ("search", "inspect"):
                painter.drawEllipse(2, 2, size - 8, size - 8)
                painter.drawLine(size - 7, size - 7, size - 2, size - 2)
            elif name in ("clear", "cancel", "close"):
                painter.drawLine(3, 3, size - 3, size - 3)
                painter.drawLine(size - 3, 3, 3, size - 3)
            elif name in ("trash", "delete"):
                painter.drawRect(4, 5, size - 8, size - 7)
                painter.drawLine(2, 5, size - 2, 5)
            elif name in ("folder", "browse", "folder_open"):
                painter.drawRect(2, 4, size - 4, size - 6)
            elif name in ("download", "export"):
                painter.drawLine(size // 2, 2, size // 2, size - 6)
                painter.drawLine(size // 2 - 4, size - 10, size // 2, size - 6)
                painter.drawLine(size // 2 + 4, size - 10, size // 2, size - 6)
                painter.drawLine(2, size - 2, size - 2, size - 2)
            elif name in ("plus", "add"):
                painter.drawLine(size // 2, 3, size // 2, size - 3)
                painter.drawLine(3, size // 2, size - 3, size // 2)
            elif name in ("select_all", "check_double"):
                painter.drawRect(2, 2, size - 4, size - 4)
                painter.drawLine(4, size // 2, size // 2, size - 4)
                painter.drawLine(size // 2, size - 4, size - 4, 4)
            elif name in ("key", "cookie"):
                painter.drawEllipse(3, 3, size - 8, size - 8)
                painter.drawLine(size - 6, size - 6, size - 2, size - 2)
            elif name in ("stop",):
                painter.drawRect(3, 3, size - 6, size - 6)
            else:
                painter.drawRect(2, 2, size - 4, size - 4)

            painter.end()
            icon = QIcon(pixmap)
            _ICON_CACHE[cache_key] = icon
            return icon
    except Exception:
        return QIcon()
