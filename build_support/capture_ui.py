"""Capture only this application's non-activating window, never the desktop."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def capture_window(window, destination):
    from PIL import Image

    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    hwnd = window.winfo_id()
    width, height = window.winfo_width(), window.winfo_height()
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 3):
            raise ctypes.WinError(ctypes.get_last_error())
        gdi.SelectObject(memory, previous)
        header = ctypes.create_string_buffer(40)
        import struct

        struct.pack_into("<IiiHHIIiiII", header, 0, 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(dc, bitmap, 0, height, pixels, header, 0) != height:
            raise RuntimeError("Could not read the application window bitmap")
        Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1).save(destination)
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--size", default="1320x860")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--black", action="store_true")
    parser.add_argument("--dpi-scale", type=float)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never write test logs or learning data into the user's real profile.
    os.environ["LOCALAPPDATA"] = str(output.parent / "test-user-data")
    from app import XiangqiApp, enable_dpi_awareness, tk
    from core import START_FEN

    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    if args.dpi_scale:
        root.tk.call("tk", "scaling", 96 * args.dpi_scale / 72)
    instance = None
    try:
        with patch("app.f1_pressed", return_value=False):
            instance = XiangqiApp(root)
            instance.auto_analysis_var.set(False)
            instance._invalidate_analysis()
            root.attributes("-topmost", False)
            instance.load_preset(START_FEN)
            if args.black:
                instance.player_side_var.set("b")
                instance._player_side_changed()
            root.geometry(f"{args.size}+24+24")
            root.deiconify()
            user = ctypes.WinDLL("user32")
            user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, wintypes.UINT]
            # Leave the user's foreground application in front of the preview.
            user.SetWindowPos(user.GetAncestor(root.winfo_id(), 2), 1, 0, 0, 0, 0, 0x13)
            if hasattr(instance, "ui"):
                instance.ui.tabs.select(args.page)
            instance.time_var.set(500)
            instance.multipv_var.set(3)
            instance.start_analysis()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                root.update()
                if instance.analysis_lines and not instance.analysis_running:
                    break
                time.sleep(0.02)
            root.update()
            capture_window(root, output)
            state = {"size": [root.winfo_width(), root.winfo_height()],
                     "canvas": [instance.canvas.winfo_width(), instance.canvas.winfo_height()],
                     "analysis_lines": len(instance.analysis_lines), "page": args.page,
                     "orientation": instance.assisted_side}
            if hasattr(instance, "ui"):
                page = instance.ui.pages[args.page]
                state["sidebar"] = {"viewport": page.canvas.winfo_height(),
                                    "content": page.content.winfo_reqheight(),
                                    "scrollable": bool(page.scrollbar.winfo_manager())}
            output.with_suffix(".json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            print(json.dumps(state), flush=True)
    finally:
        if instance is not None:
            instance._on_close()
        else:
            root.destroy()


if __name__ == "__main__":
    main()
