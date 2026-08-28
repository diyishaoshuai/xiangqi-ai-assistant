from __future__ import annotations

import ctypes
import time
from typing import Callable

from core import FILES, apply_move, piece_side, square_name


VK_F8 = 0x77
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def enable_dpi_awareness() -> None:
    """Keep screenshots and SetCursorPos in the same physical-pixel space."""
    if not hasattr(ctypes, "windll"):
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def infer_single_move(
    before: dict[tuple[int, int], str],
    after: dict[tuple[int, int], str],
    side: str,
) -> str | None:
    """Return the unique one-piece transition from before to after."""
    candidates: list[str] = []
    for start, piece in before.items():
        if piece_side(piece) != side:
            continue
        for x in range(9):
            for rank in range(10):
                end = (x, rank)
                if end == start:
                    continue
                target = before.get(end)
                if target is not None and piece_side(target) == side:
                    continue
                move = square_name(start) + square_name(end)
                if apply_move(before, move) == after:
                    candidates.append(move)
                    if len(candidates) > 1:
                        return None
    return candidates[0] if len(candidates) == 1 else None


def position_is_safe(board: dict[tuple[int, int], str]) -> bool:
    pieces = list(board.values())
    return pieces.count("K") == 1 and pieces.count("k") == 1


def f8_pressed() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_F8) & 0x8000)


def click_screen_move(
    start: tuple[float, float],
    end: tuple[float, float],
    cancelled: Callable[[], bool],
    pause_seconds: float = 0.18,
) -> None:
    """Click two screen points, retaining a cancellation gap between them."""
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("鼠标接管仅支持 Windows")
    user32 = ctypes.windll.user32

    def click(point: tuple[float, float]) -> None:
        if cancelled():
            raise InterruptedError("自动接管已停止")
        x, y = (round(point[0]), round(point[1]))
        if not user32.SetCursorPos(x, y):
            raise RuntimeError(f"无法移动鼠标到 ({x}, {y})")
        time.sleep(0.06)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.035)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    click(start)
    deadline = time.monotonic() + pause_seconds
    while time.monotonic() < deadline:
        if cancelled():
            raise InterruptedError("自动接管已停止")
        time.sleep(0.02)
    click(end)
