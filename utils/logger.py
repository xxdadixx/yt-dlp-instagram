# File: utils/logger.py
from __future__ import annotations

import logging
from PyQt6.QtCore import QObject, pyqtSignal


class QLogEmitter(QObject):
    log_record_emitted = pyqtSignal(str, int)  # (formatted_message, log_level_no)


class QtLogHandler(logging.Handler):
    """
    Thread-safe logging handler routing records across thread boundaries
    to PyQt6 UI slots via signals.
    """

    def __init__(self) -> None:
        super().__init__()
        self.emitter = QLogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.emitter.log_record_emitted.emit(msg, record.levelno)
        except Exception:
            self.handleError(record)
