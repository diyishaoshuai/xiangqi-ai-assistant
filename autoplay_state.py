"""Atomic, bounded persistence for automatic-play recovery state."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app_paths import user_data_dir
from automation import move_is_legal
from core import apply_move, make_fen, parse_fen


STATE_SCHEMA_VERSION = 1
STATE_MAX_AGE_SECONDS = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class PendingAutoplayState:
    expected_fen: str
    side: str
    history_fen: str
    moves: tuple[str, ...]
    destination_sent: bool = True


@dataclass(frozen=True, slots=True)
class AutoplayState:
    schema_version: int
    saved_at: float
    board_fen: str
    side: str
    history_fen: str
    moves: tuple[str, ...]
    platform: str
    pending: PendingAutoplayState | None = None

    def resume_tuple(self):
        board, _side = parse_fen(self.board_fen)
        pending = None
        if self.pending is not None:
            pending_board, _pending_side = parse_fen(self.pending.expected_fen)
            pending = (
                pending_board,
                self.pending.side,
                self.pending.history_fen,
                list(self.pending.moves),
            )
        return board, self.side, self.history_fen, list(self.moves), pending


def state_path() -> Path:
    return user_data_dir() / "autoplay-state.json"


def _valid_side(value) -> bool:
    return value in ("w", "b")


def _replay(history_fen: str, moves: tuple[str, ...]):
    board, side = parse_fen(history_fen)
    for move in moves:
        if not move_is_legal(board, move, side):
            raise ValueError("persisted history contains an illegal move")
        board = apply_move(board, move)
        side = "b" if side == "w" else "w"
    return board, side


def load_autoplay_state(*, max_age_seconds: float = STATE_MAX_AGE_SECONDS) -> AutoplayState | None:
    path = state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pending_payload = payload.get("pending")
        pending = None
        if pending_payload is not None:
            pending = PendingAutoplayState(
                expected_fen=str(pending_payload["expected_fen"]),
                side=str(pending_payload["side"]),
                history_fen=str(pending_payload["history_fen"]),
                moves=tuple(str(move) for move in pending_payload["moves"]),
                destination_sent=bool(pending_payload.get("destination_sent", True)),
            )
        state = AutoplayState(
            schema_version=int(payload["schema_version"]),
            saved_at=float(payload["saved_at"]),
            board_fen=str(payload["board_fen"]),
            side=str(payload["side"]),
            history_fen=str(payload["history_fen"]),
            moves=tuple(str(move) for move in payload["moves"]),
            platform=str(payload.get("platform", "generic")),
            pending=pending,
        )
        if state.schema_version != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported autoplay state schema")
        if time.time() - state.saved_at > max_age_seconds or state.saved_at > time.time() + 60:
            raise ValueError("stale autoplay state")
        if not _valid_side(state.side):
            raise ValueError("invalid side")
        board, fen_side = parse_fen(state.board_fen)
        replayed, replayed_side = _replay(state.history_fen, state.moves)
        if board != replayed or state.side != fen_side or state.side != replayed_side:
            raise ValueError("persisted board does not match move history")
        if pending is not None:
            if not _valid_side(pending.side) or not pending.destination_sent:
                raise ValueError("invalid pending transaction")
            expected, expected_side = parse_fen(pending.expected_fen)
            pending_replayed, pending_replayed_side = _replay(
                pending.history_fen, pending.moves
            )
            if (
                expected != pending_replayed
                or pending.side != expected_side
                or pending.side != pending_replayed_side
            ):
                raise ValueError("pending board does not match move history")
        return state
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        clear_autoplay_state()
        return None


def save_autoplay_state(
    board: dict[tuple[int, int], str],
    side: str,
    history_fen: str,
    moves: list[str] | tuple[str, ...],
    platform: str,
    *,
    pending_board: dict[tuple[int, int], str] | None = None,
    pending_side: str | None = None,
    pending_history_fen: str = "",
    pending_moves: list[str] | tuple[str, ...] = (),
) -> Path:
    if not _valid_side(side):
        raise ValueError("invalid side")
    pending = None
    if pending_board is not None:
        if not _valid_side(pending_side):
            raise ValueError("invalid pending side")
        pending = PendingAutoplayState(
            make_fen(pending_board, pending_side),
            pending_side,
            pending_history_fen,
            tuple(pending_moves),
            True,
        )
    state = AutoplayState(
        STATE_SCHEMA_VERSION,
        time.time(),
        make_fen(board, side),
        side,
        history_fen,
        tuple(moves),
        platform,
        pending,
    )
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def clear_autoplay_state() -> None:
    path = state_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
