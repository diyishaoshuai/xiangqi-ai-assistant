"""Bounded persistent logging, including safe rotation across EXE instances."""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import sys
import threading
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app_paths import user_data_dir


LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
APP_VERSION = "2026.09.04-adaptive-safety"


class SharedRotatingFileHandler(RotatingFileHandler):
    """Close each write before another process may rename a rotated log."""

    def __init__(self, filename: Path, *, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount,
                         encoding="utf-8", delay=True)
        self._mutex = None
        self._api = None
        if os.name == "nt":
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            api.CreateMutexW.restype = ctypes.c_void_p
            api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            api.WaitForSingleObject.restype = ctypes.c_uint32
            api.ReleaseMutex.argtypes = [ctypes.c_void_p]
            api.CloseHandle.argtypes = [ctypes.c_void_p]
            digest = hashlib.sha256(self.baseFilename.lower().encode("utf-8")).hexdigest()
            self._mutex = api.CreateMutexW(None, False, "Local\\XiangqiAI.Logs." + digest)
            if not self._mutex:
                raise ctypes.WinError(ctypes.get_last_error())
            self._api = api

    @contextmanager
    def _serialized(self):
        if self._mutex is None:
            yield
            return
        result = self._api.WaitForSingleObject(self._mutex, 5000)
        if result not in (0, 0x80):  # WAIT_OBJECT_0 or abandoned mutex after a crash.
            raise OSError("无法取得日志写入锁")
        try:
            yield
        finally:
            self._api.ReleaseMutex(self._mutex)

    def _close_stream(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def emit(self, record):
        try:
            with self._serialized():
                try:
                    super().emit(record)
                finally:
                    self._close_stream()
        except Exception:
            self.handleError(record)

    def doRollover(self):
        with self._serialized():
            super().doRollover()

    def close(self):
        self.acquire()
        try:
            super().close()
            if self._mutex is not None:
                self._api.CloseHandle(self._mutex)
                self._mutex = None
        finally:
            self.release()


def configure_logging() -> Path:
    log_dir = user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "xiangqi-ai.log"
    logger = logging.getLogger("xiangqi_ai")
    logger.setLevel(logging.DEBUG)
    # Repeated GUI/self-test initialization must not duplicate every log line.
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = SharedRotatingFileHandler(log_path)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s pid=%(process)d %(name)s %(message)s"
        ))
        logger.addHandler(handler)
    logger.propagate = False
    return log_path


def install_exception_logging() -> None:
    """Retain diagnostics without suppressing existing error reporting."""
    if getattr(sys.excepthook, "_xiangqi_logging", False):
        return
    previous = sys.excepthook

    def report(exc_type, exc_value, traceback):
        logging.getLogger("xiangqi_ai").critical(
            "unhandled application exception", exc_info=(exc_type, exc_value, traceback)
        )
        previous(exc_type, exc_value, traceback)

    report._xiangqi_logging = True
    sys.excepthook = report
    previous_thread = threading.excepthook

    def report_thread(args):
        logging.getLogger("xiangqi_ai").error(
            "unhandled background exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )
        previous_thread(args)

    threading.excepthook = report_thread
