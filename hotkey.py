"""Global F1 messages, independent of Tk redraws and model inference."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import threading

from automation import HotkeyLatch, VK_F1, f1_pressed

LOGGER = logging.getLogger("xiangqi_ai.hotkey")
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5841


class GlobalF1Hotkey:
    def __init__(self, on_press, *, api=None, key_pressed=f1_pressed):
        self.on_press = on_press
        self.api = api
        self.key_pressed = key_pressed
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.registered = False
        self.thread = threading.Thread(target=self._run, name="global-f1", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=.25)

    def _run(self):
        api = self.api
        try:
            if api is None:
                api = ctypes.WinDLL("user32", use_last_error=True)
                api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
                api.RegisterHotKey.restype = wintypes.BOOL
                api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                api.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                            wintypes.UINT, wintypes.UINT, wintypes.UINT]
                api.PeekMessageW.restype = wintypes.BOOL
            # Registration and message retrieval must belong to the same thread.
            self.registered = bool(api.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_F1))
            LOGGER.info("F1 listener mode=%s", "WM_HOTKEY" if self.registered else "background polling (key in use)")
            latch = HotkeyLatch(was_pressed=self.key_pressed())
            self.ready.set()
            message = wintypes.MSG()
            while not self.closed.is_set():
                if self.registered:
                    for _ in range(32):
                        if self.closed.is_set() or not api.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
                            break
                        if message.wParam == HOTKEY_ID:
                            self.on_press()
                elif latch.update(self.key_pressed()):
                    self.on_press()
                self.closed.wait(.01)
        except Exception:
            LOGGER.exception("F1 listener failed; UI polling remains available")
        finally:
            if self.registered and api is not None:
                api.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False
            self.ready.set()
