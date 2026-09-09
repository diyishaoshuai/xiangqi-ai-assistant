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

# ONNX occasionally changes several unrelated labels while JJ Xiangqi is
# painting selection glows, move trails, clocks, or the last-move marker.  The
# commanded move's two endpoints are much stronger evidence than those
# unrelated squares.  Keep a finite cap so an overlay/new board is never
# mistaken for the old transaction.
CLICK_ENDPOINT_MAX_MISMATCHES = 4


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
    board: dict[tuple[int, int], str] | None = None
    mismatches: int = 0


@dataclass(frozen=True)
class ClickConfirmation:
    kind: ConfirmationKind
    move: str | None = None
    board: dict[tuple[int, int], str] | None = None
    mismatches: int = 0


@dataclass(frozen=True)
class LegalPathProjection:
    moves: tuple[str, ...]
    board: dict[tuple[int, int], str]
    next_side: str
    mismatches: int = 0


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
    minimum_seconds: float = 0.0
    stable_since: float | None = None

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
        same = board == self.previous and same_geometry
        self.count = self.count + 1 if same else 1
        now = time.monotonic()
        if not same or self.stable_since is None:
            self.stable_since = now
        self.previous = dict(board)
        self.previous_geometry = geometry if hasattr(geometry, "point_for_square") else None
        return self.count >= self.required_frames and now - self.stable_since >= self.minimum_seconds

    def reset(self) -> None:
        self.previous = None
        self.count = 0
        self.previous_geometry = None
        self.stable_since = None


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
                if apply_move(before, move) == after and move_is_legal(before, move, side):
                    candidates.append(move)
                    if len(candidates) > 1:
                        return None
    return candidates[0] if len(candidates) == 1 else None


def _between_count(
    board: dict[tuple[int, int], str],
    start: tuple[int, int],
    end: tuple[int, int],
) -> int | None:
    sx, sy = start
    ex, ey = end
    if sx != ex and sy != ey:
        return None
    step_x = 0 if sx == ex else (1 if ex > sx else -1)
    step_y = 0 if sy == ey else (1 if ey > sy else -1)
    current = (sx + step_x, sy + step_y)
    count = 0
    while current != end:
        if current in board:
            count += 1
        current = (current[0] + step_x, current[1] + step_y)
    return count


def _in_palace(square: tuple[int, int], side: str) -> bool:
    x, rank = square
    ranks = range(0, 3) if side == "w" else range(7, 10)
    return 3 <= x <= 5 and rank in ranks


def _piece_move_is_pseudo_legal(
    board: dict[tuple[int, int], str],
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    piece = board.get(start)
    if piece is None or start == end:
        return False
    side = piece_side(piece)
    target = board.get(end)
    if target is not None and piece_side(target) == side:
        return False
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    kind = piece.upper()

    if kind == "K":
        if target is not None and target.upper() == "K" and sx == ex:
            return _between_count(board, start, end) == 0
        return _in_palace(end, side) and abs(dx) + abs(dy) == 1
    if kind == "A":
        return _in_palace(end, side) and abs(dx) == abs(dy) == 1
    if kind == "B":
        own_half = ey <= 4 if side == "w" else ey >= 5
        eye = (sx + dx // 2, sy + dy // 2)
        return own_half and abs(dx) == abs(dy) == 2 and eye not in board
    if kind == "N":
        if sorted((abs(dx), abs(dy))) != [1, 2]:
            return False
        leg = (
            (sx + (1 if dx > 0 else -1), sy)
            if abs(dx) == 2
            else (sx, sy + (1 if dy > 0 else -1))
        )
        return leg not in board
    if kind == "R":
        return _between_count(board, start, end) == 0
    if kind == "C":
        between = _between_count(board, start, end)
        return between == (1 if target is not None else 0)
    if kind == "P":
        forward = 1 if side == "w" else -1
        crossed = sy >= 5 if side == "w" else sy <= 4
        return (dx == 0 and dy == forward) or (
            crossed and dy == 0 and abs(dx) == 1
        )
    return False


def _square_attacked(
    board: dict[tuple[int, int], str],
    square: tuple[int, int],
    attacker_side: str,
) -> bool:
    for start, piece in board.items():
        if piece_side(piece) != attacker_side:
            continue
        # Pseudo-legal attack geometry is sufficient here; checking whether
        # the attacking side exposes its own king would recurse indefinitely.
        if _piece_move_is_pseudo_legal(board, start, square):
            return True
    return False


def move_is_legal(
    board: dict[tuple[int, int], str],
    move: str,
    side: str,
) -> bool:
    """Validate Xiangqi movement and reject moves that leave one's king checked."""
    try:
        start = (FILES.index(move[0]), int(move[1]))
        end = (FILES.index(move[2]), int(move[3]))
    except (ValueError, IndexError):
        return False
    if not all(0 <= x <= 8 and 0 <= rank <= 9 for x, rank in (start, end)):
        return False
    piece = board.get(start)
    if piece is None or piece_side(piece) != side:
        return False
    if not _piece_move_is_pseudo_legal(board, start, end):
        return False
    after = apply_move(board, move)
    king = "K" if side == "w" else "k"
    king_square = next((square for square, value in after.items() if value == king), None)
    if king_square is None:
        return False
    opponent = "b" if side == "w" else "w"
    return not _square_attacked(after, king_square, opponent)


def position_is_safe(board: dict[tuple[int, int], str]) -> bool:
    pieces = list(board.values())
    return pieces.count("K") == 1 and pieces.count("k") == 1


def position_is_terminal(board: dict[tuple[int, int], str]) -> bool:
    pieces = list(board.values())
    red_kings = pieces.count("K")
    black_kings = pieces.count("k")
    return (red_kings, black_kings) in ((1, 0), (0, 1))


def _board_mismatch_count(
    expected: dict[tuple[int, int], str],
    observed: dict[tuple[int, int], str],
) -> int:
    return sum(
        expected.get(square) != observed.get(square)
        for square in expected.keys() | observed.keys()
    )


def legal_successors(
    board: dict[tuple[int, int], str],
    side: str,
):
    for start, piece in board.items():
        if piece_side(piece) != side:
            continue
        for x in range(9):
            for rank in range(10):
                end = (x, rank)
                if end == start:
                    continue
                move = square_name(start) + square_name(end)
                if move_is_legal(board, move, side):
                    yield move, apply_move(board, move)


def project_legal_path(
    before: dict[tuple[int, int], str],
    observed: dict[tuple[int, int], str],
    side: str,
    *,
    max_plies: int = 2,
    max_mismatches: int = 1,
) -> LegalPathProjection | None:
    """Project noisy recognition onto a unique short legal continuation.

    A capture commonly leaves the captured glyph visible for one frame.  The
    cleared source square is therefore required as positive movement evidence;
    this prevents a random one-square classifier error from inventing a move.
    """
    if before == observed:
        return LegalPathProjection((), dict(before), side, 0)

    frontier = [(dict(before), side, ())]
    matches: list[LegalPathProjection] = []
    for _depth in range(1, max_plies + 1):
        next_frontier = []
        for position, turn, path in frontier:
            for move, expected in legal_successors(position, turn):
                next_turn = "b" if turn == "w" else "w"
                next_path = (*path, move)
                next_frontier.append((expected, next_turn, next_path))
                mismatch = _board_mismatch_count(expected, observed)
                start = (FILES.index(move[0]), int(move[1]))
                end = (FILES.index(move[2]), int(move[3]))
                # The mover's source must visibly agree with the projected
                # board and its destination must contain the moving piece.
                # Stale capture targets are repaired only when the commanded
                # move is already known (classify_click_confirmation below).
                if (
                    mismatch <= max_mismatches
                    and observed.get(start) == expected.get(start)
                    and observed.get(end) == expected.get(end)
                ):
                    matches.append(
                        LegalPathProjection(next_path, expected, next_turn, mismatch)
                    )
        frontier = next_frontier

    if not matches:
        return None
    best_mismatch = min(item.mismatches for item in matches)
    best = [item for item in matches if item.mismatches == best_mismatch]
    # Prefer the shortest explanation, but never guess between equal paths.
    best_depth = min(len(item.moves) for item in best)
    best = [item for item in best if len(item.moves) == best_depth]
    return best[0] if len(best) == 1 else None


def classify_board_transition(
    before: dict[tuple[int, int], str],
    after: dict[tuple[int, int], str],
    side: str,
) -> BoardTransition:
    if before == after:
        return BoardTransition(TransitionKind.SAME, board=dict(before))
    move = infer_single_move(before, after, side)
    canonical = after
    mismatches = 0
    if move is None:
        projection = project_legal_path(
            before,
            after,
            side,
            max_plies=1,
            max_mismatches=1,
        )
        if projection is None or len(projection.moves) != 1:
            return BoardTransition(TransitionKind.AMBIGUOUS)
        move = projection.moves[0]
        canonical = projection.board
        mismatches = projection.mismatches
    kind = (
        TransitionKind.TERMINAL_MOVE
        if position_is_terminal(canonical)
        else TransitionKind.MOVE
    )
    return BoardTransition(kind, move, dict(canonical), mismatches)


def classify_click_confirmation(
    before: dict[tuple[int, int], str],
    expected: dict[tuple[int, int], str],
    observed: dict[tuple[int, int], str],
    opponent_side: str,
) -> ClickConfirmation:
    if observed == before:
        return ClickConfirmation(ConfirmationKind.UNCHANGED, board=dict(before))
    if observed == expected:
        return ClickConfirmation(ConfirmationKind.EXPECTED, board=dict(expected))
    expected_mismatches = _board_mismatch_count(expected, observed)
    before_mismatches = _board_mismatch_count(before, observed)
    changed_sources = [
        square for square, piece in before.items()
        if expected.get(square) != piece and expected.get(square) is None
    ]
    changed_destinations = [
        square for square, piece in expected.items()
        if before.get(square) != piece and piece is not None
    ]
    endpoints_are_known = (
        len(changed_sources) == 1 and len(changed_destinations) == 1
    )
    source = changed_sources[0] if endpoints_are_known else None
    destination = changed_destinations[0] if endpoints_are_known else None
    unrelated_squares = (before.keys() | expected.keys() | observed.keys()) - {
        source,
        destination,
    }
    unrelated_occupancy_changes = sum(
        (expected.get(square) is None) != (observed.get(square) is None)
        for square in unrelated_squares
    )
    before_unrelated_occupancy_changes = sum(
        (before.get(square) is None) != (observed.get(square) is None)
        for square in unrelated_squares
    )
    # Prefer a uniquely legal opponent reply over endpoint-only repair.  The
    # commanded piece normally remains at its destination while the opponent
    # moves two other endpoints, so checking endpoint evidence first would
    # incorrectly erase a fast reply as classifier noise.
    transition = classify_board_transition(expected, observed, opponent_side)
    if transition.kind == TransitionKind.MOVE:
        return ClickConfirmation(
            ConfirmationKind.FAST_REPLY,
            transition.move,
            transition.board,
            transition.mismatches,
        )
    if transition.kind == TransitionKind.TERMINAL_MOVE:
        return ClickConfirmation(
            ConfirmationKind.TERMINAL_REPLY,
            transition.move,
            transition.board,
            transition.mismatches,
        )
    # Do not erase a coherent two-ply change as harmless classifier noise.  It
    # can happen when the opponent replies and the user manually moves before
    # the confirmation frame arrives.  The worker will relock instead of
    # guessing whose action should own that state.
    multi_ply = project_legal_path(
        expected,
        observed,
        opponent_side,
        max_plies=2,
        max_mismatches=0,
    )
    if multi_ply is not None and len(multi_ply.moves) == 2:
        return ClickConfirmation(ConfirmationKind.AMBIGUOUS)
    # Exact endpoint agreement proves the commanded piece left its source and
    # reached its destination.  Repair a handful of unrelated animation/glow
    # errors to the canonical legal board instead of timing out the takeover.
    if (
        endpoints_are_known
        and expected_mismatches <= CLICK_ENDPOINT_MAX_MISMATCHES
        and unrelated_occupancy_changes <= 1
        and observed.get(source) == expected.get(source)
        and observed.get(destination) == expected.get(destination)
    ):
        return ClickConfirmation(
            ConfirmationKind.EXPECTED,
            board=dict(expected),
            mismatches=expected_mismatches,
        )
    if (
        expected_mismatches <= 1
        and len(changed_sources) == 1
        and observed.get(changed_sources[0]) is None
    ):
        return ClickConfirmation(
            ConfirmationKind.EXPECTED,
            board=dict(expected),
            mismatches=expected_mismatches,
        )
    # A selected-piece glow can alter one unrelated classifier label even when
    # the actual move never left its source.  Endpoint agreement is strong
    # enough to call this unchanged, allowing one safe retry after two fresh
    # observations instead of abandoning the whole takeover session.
    if (
        before_mismatches <= CLICK_ENDPOINT_MAX_MISMATCHES
        and endpoints_are_known
        and before_unrelated_occupancy_changes <= 1
        and observed.get(source) == before.get(source)
        and observed.get(destination) == before.get(destination)
    ):
        return ClickConfirmation(
            ConfirmationKind.UNCHANGED,
            board=dict(before),
            mismatches=before_mismatches,
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


def window_process_id(window: int) -> int:
    if not window or not hasattr(ctypes, "windll"):
        return 0
    process = ctypes.c_ulong()
    api = ctypes.windll.user32
    api.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    api.GetWindowThreadProcessId(window, ctypes.byref(process))
    return int(process.value)


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
