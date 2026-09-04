"""Continuous visual evidence and transaction tracking for mouse autoplay."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import math
import os
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageGrab, ImageStat

from app_paths import user_data_dir
from automation import legal_successors, position_is_terminal
from core import piece_side


LOGGER = logging.getLogger("xiangqi_ai.tracking")
PIECE_LABELS = ("K", "A", "B", "N", "R", "C", "P", "k", "a", "b", "n", "r", "c", "p")
MODEL_LABELS = (".", "x", *PIECE_LABELS)


@dataclass(frozen=True, slots=True)
class SquareEvidence:
    empty: float
    red: float
    black: float
    pieces: dict[str, float]
    occluded: float = 0.0
    motion: float = 0.0

    def probability(self, piece: str | None) -> float:
        if piece is None:
            return max(1e-6, self.empty)
        type_probability = self.pieces.get(piece, 0.0)
        side_probability = self.red if piece.isupper() else self.black
        # Occupancy/side remains useful when a selection or mate glow obscures
        # the glyph, while the type distribution still carries most weight.
        return max(1e-6, type_probability * 0.68 + side_probability * 0.32)


@dataclass(frozen=True, slots=True)
class BoardObservation:
    squares: dict[tuple[int, int], SquareEvidence]
    geometry: Any
    decoded_board: dict[tuple[int, int], str]
    stable: bool
    platform: str
    overlay_score: float = 0.0
    captured_at: float = field(default_factory=time.monotonic)

    def endpoint_matches(
        self,
        source: tuple[int, int],
        destination: tuple[int, int],
        moving_piece: str,
        *,
        threshold: float = 0.62,
    ) -> bool:
        source_evidence = self.squares[source]
        destination_evidence = self.squares[destination]
        destination_side = destination_evidence.red if moving_piece.isupper() else destination_evidence.black
        return source_evidence.empty >= threshold and destination_side >= threshold

    def endpoint_is_unchanged(
        self,
        source: tuple[int, int],
        destination: tuple[int, int],
        before: dict[tuple[int, int], str],
        *,
        threshold: float = 0.66,
    ) -> bool:
        return (
            self.squares[source].probability(before.get(source)) >= threshold
            and self.squares[destination].probability(before.get(destination)) >= threshold
        )


def _probabilities(values: Iterable[float]) -> list[float]:
    raw = [float(value) for value in values]
    if raw and all(0.0 <= value <= 1.0 for value in raw) and 0.97 <= sum(raw) <= 1.03:
        total = max(sum(raw), 1e-9)
        return [value / total for value in raw]
    maximum = max(raw, default=0.0)
    exponential = [math.exp(max(-60.0, min(60.0, value - maximum))) for value in raw]
    total = max(sum(exponential), 1e-9)
    return [value / total for value in exponential]


def evidence_from_model_scores(scores, *, rotated: bool) -> dict[tuple[int, int], SquareEvidence]:
    """Convert the model's 90x16 distribution into occupancy and piece evidence."""
    result: dict[tuple[int, int], SquareEvidence] = {}
    for row in range(10):
        for column in range(9):
            probabilities = _probabilities(scores[row * 9 + column])
            by_label = dict(zip(MODEL_LABELS, probabilities))
            square = (8 - column, row) if rotated else (column, 9 - row)
            red = sum(by_label.get(piece, 0.0) for piece in PIECE_LABELS[:7])
            black = sum(by_label.get(piece, 0.0) for piece in PIECE_LABELS[7:])
            result[square] = SquareEvidence(
                empty=by_label.get(".", 0.0),
                red=red,
                black=black,
                pieces={piece: by_label.get(piece, 0.0) for piece in PIECE_LABELS},
                occluded=by_label.get("x", 0.0),
            )
    return result


def add_frame_motion(
    evidence: dict[tuple[int, int], SquareEvidence],
    previous: Image.Image | None,
    current: Image.Image,
    geometry,
) -> tuple[dict[tuple[int, int], SquareEvidence], bool]:
    """Attach per-square motion without allowing unrelated UI to affect it."""
    if previous is None or previous.size != current.size:
        return evidence, True
    origin = geometry.point_for_square((0, 0))
    neighbour = geometry.point_for_square((1, 0))
    radius = max(3, round(math.dist(origin, neighbour) * 0.24))
    old_gray = previous.convert("L")
    new_gray = current.convert("L")
    result = {}
    motion_values = []
    for square, value in evidence.items():
        x, y = geometry.point_for_square(square)
        bounds = (
            max(0, round(x) - radius),
            max(0, round(y) - radius),
            min(current.width, round(x) + radius + 1),
            min(current.height, round(y) + radius + 1),
        )
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            motion = 1.0
        else:
            delta = ImageStat.Stat(
                ImageChops.difference(old_gray.crop(bounds), new_gray.crop(bounds))
            ).mean[0]
            motion = min(1.0, delta / 24.0)
        motion_values.append(motion)
        result[square] = replace(value, motion=motion)
    # Selection glows may animate one or two cells.  The board as a whole is
    # considered unstable only when a meaningful fraction of intersections move.
    moving = sum(value >= 0.12 for value in motion_values)
    return result, moving <= 4


def track_geometry_with_optical_flow(
    previous: Image.Image | None,
    current: Image.Image,
    geometry,
):
    """Translate a locked homography using sparse Lucas-Kanade optical flow.

    Returns ``(geometry_or_none, displacement, reliable)``.  ``None`` means
    that the board moved by more than half a cell and the pose model must
    perform a fresh full lock.
    """
    if previous is None or previous.size != current.size:
        return geometry, 0.0, False
    try:
        import cv2
        import numpy as np

        bounds = board_bounds(geometry, margin_ratio=0.05)
        old = np.asarray(previous.crop(bounds).convert("L"))
        new = np.asarray(current.crop(bounds).convert("L"))
        points = cv2.goodFeaturesToTrack(
            old, maxCorners=80, qualityLevel=0.015, minDistance=7, blockSize=7
        )
        if points is None or len(points) < 8:
            return geometry, 0.0, False
        moved, status, errors = cv2.calcOpticalFlowPyrLK(
            old,
            new,
            points,
            None,
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.01),
        )
        if moved is None or status is None:
            return geometry, 0.0, False
        valid = status.reshape(-1).astype(bool)
        if errors is not None:
            valid &= errors.reshape(-1) < 30.0
        if int(valid.sum()) < 8:
            return geometry, 0.0, False
        delta = moved.reshape(-1, 2)[valid] - points.reshape(-1, 2)[valid]
        dx, dy = np.median(delta, axis=0)
        residual = np.linalg.norm(delta - np.array([dx, dy]), axis=1)
        origin = geometry.point_for_square((0, 0))
        neighbour = geometry.point_for_square((1, 0))
        cell = math.dist(origin, neighbour)
        if float(np.median(residual)) > max(2.0, cell * 0.16):
            return geometry, 0.0, False
        displacement = math.hypot(float(dx), float(dy))
        if displacement > cell * 0.50:
            return None, displacement, True
        if displacement < 0.35:
            return geometry, displacement, True
        matrix = list(geometry.inverse_matrix)
        g, h, i = matrix[6], matrix[7], matrix[8]
        matrix[0] += float(dx) * g
        matrix[1] += float(dx) * h
        matrix[2] += float(dx) * i
        matrix[3] += float(dy) * g
        matrix[4] += float(dy) * h
        matrix[5] += float(dy) * i
        return replace(geometry, inverse_matrix=tuple(matrix)), displacement, True
    except Exception as exc:
        LOGGER.debug("optical-flow geometry tracking unavailable: %s", exc)
        return geometry, 0.0, False


@dataclass(frozen=True, slots=True)
class Estimate:
    kind: str
    board: dict[tuple[int, int], str] | None
    moves: tuple[str, ...] = ()
    score: float = float("-inf")
    margin: float = 0.0
    accepted: bool = False


class LegalStateEstimator:
    """Decode uncertain visual evidence against reachable legal positions."""

    def __init__(self, *, required_frames: int = 2, minimum_margin: float = 0.05):
        self.required_frames = required_frames
        self.minimum_margin = minimum_margin
        self._last_key = None
        self._count = 0

    @staticmethod
    def _score(
        observation: BoardObservation,
        board: dict[tuple[int, int], str],
        endpoints: set[tuple[int, int]],
    ) -> float:
        total = weight_sum = 0.0
        for square, evidence in observation.squares.items():
            weight = 3.0 if square in endpoints else 1.0
            weight *= max(0.12, 1.0 - evidence.occluded * 0.85)
            weight *= max(0.18, 1.0 - evidence.motion * 0.80)
            total += weight * math.log(evidence.probability(board.get(square)))
            weight_sum += weight
        return total / max(weight_sum, 1e-9)

    def observe(
        self,
        observation: BoardObservation,
        current: dict[tuple[int, int], str],
        side: str,
        *,
        pending_move: str | None = None,
        expected: dict[tuple[int, int], str] | None = None,
    ) -> Estimate:
        def board_key(board):
            return tuple(sorted(board.items()))

        # A pending transaction's named states replace identical generic legal
        # states.  Keeping both would manufacture a zero score margin and make
        # an otherwise certain observation impossible to commit.
        candidates_by_board = {board_key(current): ("same", (), dict(current))}
        for move, board in legal_successors(current, side):
            candidates_by_board[board_key(board)] = ("move", (move,), board)
        if pending_move is not None and expected is not None:
            candidates_by_board[board_key(expected)] = ("expected", (pending_move,), dict(expected))
            opponent = "b" if side == "w" else "w"
            for reply, board in legal_successors(expected, opponent):
                candidates_by_board[board_key(board)] = ("fast_reply", (pending_move, reply), board)
        candidates = list(candidates_by_board.values())

        endpoints: set[tuple[int, int]] = set()
        if pending_move and len(pending_move) == 4:
            from core import parse_move
            endpoints.update(parse_move(pending_move))
        ranked = sorted(
            ((self._score(observation, board, endpoints), kind, moves, board)
             for kind, moves, board in candidates),
            reverse=True,
            key=lambda item: item[0],
        )
        best_score, kind, moves, board = ranked[0]
        margin = best_score - ranked[1][0] if len(ranked) > 1 else float("inf")
        key = (kind, moves)
        self._count = self._count + 1 if key == self._last_key else 1
        self._last_key = key
        required = 1 if kind == "fast_reply" and margin >= self.minimum_margin * 2 else self.required_frames
        accepted = margin >= self.minimum_margin and self._count >= required
        if accepted and position_is_terminal(board):
            kind = "terminal"
        return Estimate(kind, dict(board), moves, best_score, margin, accepted)

    def reset(self) -> None:
        self._last_key = None
        self._count = 0


class TransactionState(str, Enum):
    PREPARED = "prepared"
    SOURCE_SENT = "source_sent"
    DESTINATION_SENT = "destination_sent"
    OBSERVING = "observing"
    COMMITTED = "committed"
    TERMINAL = "terminal"
    UNCERTAIN = "uncertain"


@dataclass(slots=True)
class MoveTransaction:
    move: str
    before: dict[tuple[int, int], str]
    expected: dict[tuple[int, int], str]
    terminal_hint: bool = False
    state: TransactionState = TransactionState.PREPARED
    destination_send_count: int = 0
    created_at: float = field(default_factory=time.monotonic)
    destination_sent_at: float | None = None
    diagnostic_saved: bool = False

    def source_sent(self) -> None:
        if self.state != TransactionState.PREPARED:
            raise RuntimeError("source click cannot be sent twice")
        self.state = TransactionState.SOURCE_SENT

    def destination_sent(self) -> None:
        if self.destination_send_count:
            raise RuntimeError("destination click cannot be sent twice")
        if self.state == TransactionState.PREPARED:
            self.state = TransactionState.SOURCE_SENT
        if self.state != TransactionState.SOURCE_SENT:
            raise RuntimeError("destination click is out of order")
        self.destination_send_count = 1
        self.destination_sent_at = time.monotonic()
        self.state = TransactionState.DESTINATION_SENT

    def observe(self) -> None:
        if self.state == TransactionState.DESTINATION_SENT:
            self.state = TransactionState.OBSERVING

    def commit(self, *, terminal: bool = False) -> None:
        if self.destination_send_count != 1:
            raise RuntimeError("an unsent transaction cannot commit")
        self.state = TransactionState.TERMINAL if terminal else TransactionState.COMMITTED

    def uncertain(self) -> None:
        if self.destination_send_count == 1:
            self.state = TransactionState.UNCERTAIN

    @property
    def elapsed(self) -> float:
        return time.monotonic() - (self.destination_sent_at or self.created_at)


@dataclass(frozen=True, slots=True)
class PlatformProfile:
    name: str
    animation_seconds: float
    endpoint_threshold: float
    terminal_overlay_threshold: float


JJ_PROFILE = PlatformProfile("jj", 1.60, 0.58, 0.28)
TIANTIAN_PROFILE = PlatformProfile("tiantian", 0.75, 0.62, 0.34)
GENERIC_PROFILE = PlatformProfile("generic", 1.20, 0.64, 0.38)


def select_platform_profile(window_title: str = "", process_name: str = "") -> PlatformProfile:
    identity = f"{window_title} {process_name}".lower()
    if "jj" in identity or "竞技世界" in identity:
        return JJ_PROFILE
    if "天天象棋" in identity or "tian tian" in identity or "tiantian" in identity:
        return TIANTIAN_PROFILE
    return GENERIC_PROFILE


def window_identity(hwnd: int) -> tuple[str, str]:
    if os.name != "nt" or not hwnd:
        return "", ""
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_name = ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                process_name = Path(path_buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    return title_buffer.value, process_name


class FrameSource:
    """DXGI-backed capture with a Pillow fallback and an in-memory ring."""

    def __init__(self, *, ring_size: int = 12, target_fps: int = 20, dxcam_module=None):
        self._dxcam_module = dxcam_module
        self._camera = None
        self._disabled = False
        self.target_fps = max(1, min(60, target_fps))
        self._lock = threading.Lock()
        self._last: Image.Image | None = None
        self.frames: deque[tuple[float, Image.Image]] = deque(maxlen=ring_size)
        self.backend = "pillow"

    def _ensure_camera(self):
        if self._disabled or self._camera is not None:
            return self._camera
        try:
            module = self._dxcam_module
            if module is None:
                import dxcam as module
            self._camera = module.create(output_color="RGB")
            self._camera.start(target_fps=self.target_fps, video_mode=True)
            self.backend = "dxcam"
        except Exception as exc:
            self._disabled = True
            LOGGER.info("DXcam unavailable; using Pillow capture: %s", exc)
        return self._camera

    def grab(self) -> Image.Image:
        with self._lock:
            image = None
            camera = self._ensure_camera()
            if camera is not None:
                try:
                    frame = camera.get_latest_frame()
                    if frame is None:
                        frame = camera.grab()
                    if frame is not None:
                        image = Image.fromarray(frame).convert("RGB")
                    elif self._last is not None:
                        image = self._last.copy()
                except Exception as exc:
                    LOGGER.warning("DXcam capture failed; permanently falling back: %s", exc)
                    self._disabled = True
                    self.backend = "pillow"
                    self._camera = None
            if image is None:
                image = ImageGrab.grab().convert("RGB")
            self._last = image.copy()
            self.frames.append((time.monotonic(), image.copy()))
            return image

    def close(self) -> None:
        with self._lock:
            camera, self._camera = self._camera, None
            if camera is not None:
                try:
                    camera.stop()
                except Exception:
                    pass


def board_bounds(geometry, *, margin_ratio: float = 0.60) -> tuple[int, int, int, int]:
    corners = [geometry.point_for_square(square) for square in ((0, 0), (8, 0), (0, 9), (8, 9))]
    step = max(math.dist(corners[0], corners[1]) / 8, math.dist(corners[0], corners[2]) / 9)
    margin = max(4, round(step * margin_ratio))
    width, height = geometry.image_size
    return (
        max(0, math.floor(min(x for x, _ in corners)) - margin),
        max(0, math.floor(min(y for _, y in corners)) - margin),
        min(width, math.ceil(max(x for x, _ in corners)) + margin),
        min(height, math.ceil(max(y for _, y in corners)) + margin),
    )


def overlay_likelihood(reference: Image.Image | None, current: Image.Image, geometry) -> float:
    if reference is None or reference.size != current.size:
        return 0.0
    bounds = board_bounds(geometry, margin_ratio=0.05)
    old = reference.crop(bounds).convert("L").resize((90, 100))
    new = current.crop(bounds).convert("L").resize((90, 100))
    difference = ImageChops.difference(old, new)
    changed = sum(value >= 28 for value in difference.getdata()) / 9000.0
    # A large result card/overlay changes a broad portion of the board. Piece
    # animation changes only a few local cells and remains below this measure.
    return changed


class DiagnosticRecorder:
    def __init__(self, root: Path | None = None, *, max_events: int = 5, max_bytes: int = 100 * 1024 * 1024):
        self.root = root or (user_data_dir() / "diagnostics")
        self.max_events = max_events
        self.max_bytes = max_bytes

    def save(self, event: str, frames, metadata: dict[str, Any], geometry=None) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.root / f"{stamp}-{event}"
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.root / f"{stamp}-{event}-{suffix}"
        target.mkdir()
        bounds = board_bounds(geometry) if geometry is not None else None
        selected = list(frames)[-12:]
        for index, (_captured_at, image) in enumerate(selected):
            sample = image.crop(bounds) if bounds is not None and image.size == geometry.image_size else image
            sample.save(target / f"frame-{index:02d}.png", optimize=True)
        payload = dict(metadata)
        payload["event"] = event
        payload["saved_at"] = time.time()
        payload["frame_count"] = len(selected)
        (target / "state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.prune()
        return target

    def prune(self) -> None:
        if not self.root.exists():
            return
        entries = sorted((path for path in self.root.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime)
        def size(path):
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        total = sum(size(path) for path in entries)
        while entries and (len(entries) > self.max_events or total > self.max_bytes):
            oldest = entries.pop(0)
            total -= size(oldest)
            shutil.rmtree(oldest, ignore_errors=True)

    def clear(self) -> None:
        if self.root.exists():
            for child in self.root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
