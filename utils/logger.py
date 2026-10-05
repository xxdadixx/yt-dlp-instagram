"""
utils/logger.py - Thread-safe Qt log emission bridge with C++ lifecycle safety.
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.sip import isdeleted


class QLogEmitter(QObject):
    log_record_emitted = pyqtSignal(str, int)


class QtLogHandler(logging.Handler):
    """Bridges standard logging records to Qt GUI signals.

    Defensively guards against C++ object deletion during worker teardown and application shutdown.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__()
        self.emitter = QLogEmitter(parent)
        self._is_closed: bool = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._is_closed:
            return

        try:
            # Prevent calling .emit on a deallocated C++ QObject
            if isdeleted(self.emitter):
                return

            msg = self.format(record)
            self.emitter.log_record_emitted.emit(msg, record.levelno)
        except (RuntimeError, ReferenceError):
            # Underlying C++ QObject was destroyed concurrently during Qt teardown
            pass
        except Exception:
            if not self._is_closed:
                self.handleError(record)

    def close(self) -> None:
        """Detaches signals and marks the handler closed to prevent dead emissions."""
        self._is_closed = True
        try:
            if not isdeleted(self.emitter):
                self.emitter.log_record_emitted.disconnect()
        except Exception:
            pass
        super().close()
