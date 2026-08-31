from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from core import FILES, apply_move, piece_side, square_name


VK_F1 = 0x70
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
GA_ROOT = 2


class AutomationState(str, Enum):
    IDLE = "idle"
    ACQUIRING = "acquiring"
    WAITING_OPPONENT = "waiting_opponent"
    THINKING = "thinking"
    WAITING_USER_IDLE = "waiting_user_idle"
    CLICKING = "clicking"
    CONFIRMING = "confirming"
    WAITING_BOARD = "waiting_board"
    WAITING_NEXT_GAME = "waiting_next_game"
    STOPPING = "stopping"


class TransitionKind(str, Enum):
    SAME = "same"
    MOVE = "move"
    TERMINAL_MOVE = "terminal_move"
    AMBIGUOUS = "ambiguous"


class ConfirmationKind(str, Enum):
    UNCHANGED = "unchanged"
    EXPECTED = "expected"
    FAST_REPLY = "fast_reply"
    TERMINAL_REPLY = "terminal_reply"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class BoardTransition:
    kind: TransitionKind
    move: str | None = None


@dataclass(frozen=True)
class ClickConfirmation:
    kind: ConfirmationKind
    move: str | None = None


@dataclass(frozen=True)
class ClickResult:
    """Evidence that both clicks in one move transaction were sent."""

    completed: bool
    first_click_sent: bool
    cursor_attempts: int


@dataclass
class StableBoardTracker:
    """Require consecutive accepted copies of a board before trusting it."""

    required_frames: int
    previous: dict[tuple[int, int], str] | None = None
    count: int = 0
    previous_geometry: object = None

    def observe(
        self,
        board: dict[tuple[int, int], str],
        *,
        accepted: bool = True,
        geometry=None,
    ) -> bool:
        if not accepted:
            self.reset()
            return False
        same_geometry = True
        if geometry is not None and hasattr(geometry, "point_for_square") and self.previous_geometry is not None:
            previous = self.previous_geometry
            squares = ((0, 0), (8, 0), (0, 9), (8, 9))
            tolerance = max(2.0, math.dist(geometry.point_for_square((0, 0)),
                                          geometry.point_for_square((1, 0))) * 0.12)
            same_geometry = (
                geometry.image_size == previous.image_size
                and geometry.rotated == previous.rotated
                and all(math.dist(geometry.point_for_square(square), previous.point_for_square(square)) <= tolerance
                        for square in squares)
            )
        self.count = self.count + 1 if board == self.previous and same_geometry else 1
        self.previous = dict(board)
        self.previous_geometry = geometry if hasattr(geometry, "point_for_square") else None
        return self.count >= self.required_frames

    def reset(self) -> None:
        self.previous = None
        self.count = 0
        self.previous_geometry = None


@dataclass
class HotkeyLatch:
    """Turn a polled key level into one debounced press event."""

    debounce_seconds: float = 0.25
    was_pressed: bool = False
    last_triggered_at: float = -1_000_000.0

    def update(self, pressed: bool, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        triggered = (
            pressed
            and not self.was_pressed
            and timestamp - self.last_triggered_at >= self.debounce_seconds
        )
        self.was_pressed = pressed
        if triggered:
            self.last_triggered_at = timestamp
        return triggered


def session_event_is_current(
    event_session_id: int,
    current_session_id: int,
    *,
    running: bool,
    stopping: bool = False,
) -> bool:
    """Reject late worker messages after stop or a rapid session restart."""
    return (
        running
        and not stopping
        and event_session_id == current_session_id
    )


class RecoverableAutomationError(RuntimeError):
    """A temporary desktop/input failure that must not end takeover mode."""

    def __init__(self, message: str, *, first_click_sent: bool = False):
        super().__init__(message)
        self.first_click_sent = first_click_sent


class FatalAutomationError(RuntimeError):
    """A missing component or invalid configuration that cannot self-heal."""


class CursorUnavailableError(RecoverableAutomationError):
    pass


class UserInterferenceError(RecoverableAutomationError):
    pass


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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


def position_is_terminal(board: dict[tuple[int, int], str]) -> bool:
    pieces = list(board.values())
    red_kings = pieces.count("K")
    black_kings = pieces.count("k")
    return (red_kings, black_kings) in ((1, 0), (0, 1))


def classify_board_transition(
    before: dict[tuple[int, int], str],
    after: dict[tuple[int, int], str],
    side: str,
) -> BoardTransition:
    if before == after:
        return BoardTransition(TransitionKind.SAME)
    move = infer_single_move(before, after, side)
    if move is None:
        return BoardTransition(TransitionKind.AMBIGUOUS)
    kind = (
        TransitionKind.TERMINAL_MOVE
        if position_is_terminal(after)
        else TransitionKind.MOVE
    )
    return BoardTransition(kind, move)


def classify_click_confirmation(
    before: dict[tuple[int, int], str],
    expected: dict[tuple[int, int], str],
    observed: dict[tuple[int, int], str],
    opponent_side: str,
) -> ClickConfirmation:
    if observed == before:
        return ClickConfirmation(ConfirmationKind.UNCHANGED)
    if observed == expected:
        return ClickConfirmation(ConfirmationKind.EXPECTED)
    transition = classify_board_transition(expected, observed, opponent_side)
    if transition.kind == TransitionKind.MOVE:
        return ClickConfirmation(ConfirmationKind.FAST_REPLY, transition.move)
    if transition.kind == TransitionKind.TERMINAL_MOVE:
        return ClickConfirmation(
            ConfirmationKind.TERMINAL_REPLY,
            transition.move,
        )
    return ClickConfirmation(ConfirmationKind.AMBIGUOUS)


def turn_for_new_game(
    board: dict[tuple[int, int], str],
    standard_board: dict[tuple[int, int], str],
    session_start_turn: str,
) -> str:
    return "w" if board == standard_board else session_start_turn


def f1_pressed() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_F1) & 0x8000)


def seconds_since_last_input() -> float:
    if not hasattr(ctypes, "windll"):
        return float("inf")
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return float("inf")
    current = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    elapsed_ms = (current - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def user_input_is_idle(
    minimum_seconds: float = 1.2,
    *,
    elapsed_seconds: float | None = None,
) -> bool:
    elapsed = (
        seconds_since_last_input()
        if elapsed_seconds is None
        else elapsed_seconds
    )
    return elapsed >= minimum_seconds


def foreground_window() -> int:
    if not hasattr(ctypes, "windll"):
        return 0
    api = ctypes.windll.user32
    api.GetForegroundWindow.restype = ctypes.c_void_p
    return int(api.GetForegroundWindow() or 0)


def window_at_point(point: tuple[float, float]) -> int:
    if not hasattr(ctypes, "windll"):
        return 0
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.WindowFromPoint.argtypes = [_Point]
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    child = user32.WindowFromPoint(_Point(round(point[0]), round(point[1])))
    if not child:
        return 0
    return int(user32.GetAncestor(child, GA_ROOT) or child)


def _cursor_position(user32) -> tuple[int, int] | None:
    point = _Point()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


def _move_cursor_verified(
    user32,
    point: tuple[float, float],
    cancelled: Callable[[], bool],
    *,
    retries: int,
    tolerance: int,
    sleep: Callable[[float], None],
) -> tuple[int, int, int]:
    x, y = round(point[0]), round(point[1])
    for attempt in range(1, retries + 1):
        if cancelled():
            raise InterruptedError("自动接管已停止")
        moved = bool(user32.SetCursorPos(x, y))
        actual = _cursor_position(user32) if moved else None
        if actual is not None and abs(actual[0] - x) <= tolerance and abs(actual[1] - y) <= tolerance:
            return x, y, attempt
        if attempt < retries:
            sleep(0.05)
    error_code = int(ctypes.get_last_error())
    suffix = f"，Win32 错误 {error_code}" if error_code else ""
    raise CursorUnavailableError(f"暂时无法移动鼠标到 ({x}, {y}){suffix}")


def click_screen_move(
    start: tuple[float, float],
    end: tuple[float, float],
    cancelled: Callable[[], bool],
    pause_seconds: float = 0.18,
    *,
    guard: Callable[[], bool] | None = None,
    move_retries: int = 3,
    cursor_tolerance: int = 3,
    settle_seconds: float = 0.06,
    user32=None,
    sleep: Callable[[float], None] = time.sleep,
) -> ClickResult:
    """Click a move after verified cursor placement, preserving an F1 gap."""
    if user32 is None and not hasattr(ctypes, "windll"):
        raise RuntimeError("鼠标接管仅支持 Windows")
    user32 = user32 or ctypes.windll.user32
    first_click_sent = False
    cursor_attempts = 0

    def click(point: tuple[float, float]) -> None:
        nonlocal cursor_attempts, first_click_sent
        if cancelled():
            raise InterruptedError("自动接管已停止")
        if guard is not None and not guard():
            raise UserInterferenceError(
                "游戏窗口已切换，等待返回后继续",
                first_click_sent=first_click_sent,
            )
        _, _, attempts = _move_cursor_verified(
            user32,
            point,
            cancelled,
            retries=move_retries,
            tolerance=cursor_tolerance,
            sleep=sleep,
        )
        cursor_attempts += attempts
        sleep(settle_seconds)
        # Cancellation may arrive after SetCursorPos, before the button press.
        if cancelled():
            raise InterruptedError("自动接管已停止")
        if guard is not None and not guard():
            raise UserInterferenceError("游戏窗口已切换", first_click_sent=first_click_sent)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        try:
            sleep(0.035)
        finally:
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        first_click_sent = True

    try:
        click(start)
        deadline = time.monotonic() + pause_seconds
        while time.monotonic() < deadline:
            if cancelled():
                raise InterruptedError("自动接管已停止")
            if guard is not None and not guard():
                raise UserInterferenceError(
                    "点击过程中窗口发生变化，等待重新锁定",
                    first_click_sent=True,
                )
            current = _cursor_position(user32)
            if current is not None:
                start_xy = (round(start[0]), round(start[1]))
                if (
                    abs(current[0] - start_xy[0]) > cursor_tolerance
                    or abs(current[1] - start_xy[1]) > cursor_tolerance
                ):
                    raise UserInterferenceError(
                        "检测到用户正在移动鼠标，已暂停本次点击",
                        first_click_sent=True,
                    )
            sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        click(end)
        return ClickResult(
            completed=True,
            first_click_sent=True,
            cursor_attempts=cursor_attempts,
        )
    except RecoverableAutomationError as exc:
        exc.first_click_sent = exc.first_click_sent or first_click_sent
        raise
