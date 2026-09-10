from __future__ import annotations

import os
import logging
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from core import AnalysisLine


INFO_RE = re.compile(
    r"\bdepth (?P<depth>\d+).*?\bmultipv (?P<multipv>\d+).*?"
    r"\bscore (?P<score_type>cp|mate) (?P<score>-?\d+).*?\bpv (?P<pv>.+)$"
)
WDL_RE = re.compile(r"\bwdl (?P<win>\d+) (?P<draw>\d+) (?P<loss>\d+)\b")
MOVE_RE = re.compile(r"^[a-i][0-9][a-i][0-9]$")
LOGGER = logging.getLogger("xiangqi_ai.engine")


class EngineError(RuntimeError):
    pass


@dataclass(slots=True)
class EngineSearchResult:
    """One internally consistent, completed UCI search result.

    Iteration support deliberately preserves the historic ``lines, bestmove =``
    call pattern while callers migrate to the richer metadata.
    """

    lines: list[AnalysisLine]
    bestmove: str
    ponder: str | None
    completed_depth: int
    root_stability: int
    trusted: bool
    trust_reason: str
    elapsed_ms: float

    def __iter__(self) -> Iterator[object]:
        yield self.lines
        yield self.bestmove

    @property
    def primary(self) -> AnalysisLine | None:
        return self.lines[0] if self.lines else None


class PikafishEngine:
    def __init__(self, executable: Path, threads: int = 4, hash_mb: int = 256):
        self.executable = executable
        self.process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._search_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._closed = False
        self.threads = threads
        self.hash_mb = hash_mb

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise EngineError("引擎已关闭，不能重新启动")
            if self.process and self.process.poll() is None:
                return
            if not self.executable.exists():
                raise EngineError(f"找不到引擎：{self.executable}")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.process = subprocess.Popen(
                [str(self.executable)],
                cwd=str(self.executable.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        self._send("uci")
        self._read_until("uciok")
        self._send(f"setoption name Threads value {self.threads}")
        self._send(f"setoption name Hash value {self.hash_mb}")
        self._send("setoption name UCI_ShowWDL value true")
        # Pikafish on Windows may fail to open an NNUE path containing CJK
        # characters. The engine process already runs in executable.parent, so
        # prefer a short relative path even when the app itself is installed in
        # a Chinese-named directory.
        if (self.executable.parent / "pikafish.nnue").exists():
            self._send("setoption name EvalFile value pikafish.nnue")
        elif (self.executable.parent.parent / "pikafish.nnue").exists():
            self._send(r"setoption name EvalFile value ..\pikafish.nnue")
        self._send("isready")
        self._read_until("readyok")

    def _send(self, command: str) -> None:
        with self._write_lock:
            process = self.process
            if not process or not process.stdin:
                raise EngineError("引擎尚未启动")
            try:
                process.stdin.write(command + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise EngineError("引擎已退出") from exc

    def _read_until(self, token: str) -> list[str]:
        process = self.process
        if not process or not process.stdout:
            raise EngineError("引擎尚未启动")
        lines: list[str] = []
        while True:
            try:
                line = process.stdout.readline()
            except (OSError, ValueError) as exc:
                raise EngineError("引擎已退出") from exc
            if line == "":
                raise EngineError("引擎意外退出")
            clean = line.strip()
            lines.append(clean)
            if clean == token or clean.startswith(token + " "):
                return lines

    def analyse(
        self,
        fen: str,
        movetime_ms: int,
        multipv: int,
        *,
        history_fen: str | None = None,
        moves: list[str] | tuple[str, ...] | None = None,
        root_moves: Sequence[str] | None = None,
        cancelled=None,
    ) -> EngineSearchResult:
        with self._search_lock:
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            self.start()
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            started = time.monotonic()
            move_history = list(moves or ())
            LOGGER.info(
                "analysis start fen=%s history_fen=%s moves=%s movetime_ms=%s multipv=%s root_moves=%s",
                fen,
                history_fen or fen,
                " ".join(move_history) or "-",
                movetime_ms,
                multipv,
                " ".join(root_moves or ()) or "-",
            )
            self._send(f"setoption name MultiPV value {multipv}")
            position_command = f"position fen {history_fen or fen}"
            if move_history:
                position_command += " moves " + " ".join(move_history)
            self._send(position_command)
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            normalized_root_moves = tuple(move.lower() for move in (root_moves or ()))
            if any(not MOVE_RE.fullmatch(move) for move in normalized_root_moves):
                raise EngineError("受限搜索包含无效着法")
            go_command = f"go movetime {movetime_ms}"
            if normalized_root_moves:
                go_command += " searchmoves " + " ".join(normalized_root_moves)
            self._send(go_command)
            # Cover stop arriving immediately before/while sending 'go'. Drain
            # that search's bestmove before releasing the shared engine lock.
            stop_sent = cancelled is not None and cancelled()
            if stop_sent:
                self._send("stop")
            process = self.process
            if not process or not process.stdout:
                raise EngineError("引擎尚未启动")
            iterations: dict[int, dict[int, tuple[AnalysisLine, str | None]]] = {}
            bestmove = ""
            ponder = None
            output_tail: deque[str] = deque(maxlen=12)
            last_depth_log = -float("inf")
            while True:
                if not stop_sent and cancelled is not None and cancelled():
                    self._send("stop")
                    stop_sent = True
                try:
                    line = process.stdout.readline()
                except (OSError, ValueError) as exc:
                    raise EngineError("引擎已退出") from exc
                if line == "":
                    details = "\n".join(output_tail)
                    suffix = f"\n\n引擎最后输出：\n{details}" if details else ""
                    raise EngineError(f"分析过程中引擎退出{suffix}")
                clean = line.strip()
                if clean:
                    output_tail.append(clean)
                    now = time.monotonic()
                    # Parse every PV, but don't open/flush a file thousands of
                    # times during one short search. Keep full error tails above.
                    if not clean.startswith("info depth ") or now - last_depth_log >= .25:
                        LOGGER.debug("engine << %s", clean)
                        if clean.startswith("info depth "):
                            last_depth_log = now
                match = INFO_RE.search(clean)
                if match:
                    wdl_match = WDL_RE.search(clean)
                    wdl = (
                        (
                            int(wdl_match.group("win")),
                            int(wdl_match.group("draw")),
                            int(wdl_match.group("loss")),
                        )
                        if wdl_match
                        else None
                    )
                    item = AnalysisLine(
                        multipv=int(match.group("multipv")),
                        depth=int(match.group("depth")),
                        score_type=match.group("score_type"),
                        score=int(match.group("score")),
                        pv=match.group("pv").split(),
                        wdl=wdl,
                    )
                    bound = (
                        "lowerbound" if " lowerbound " in f" {clean} "
                        else "upperbound" if " upperbound " in f" {clean} "
                        else None
                    )
                    iterations.setdefault(item.depth, {})[item.multipv] = (item, bound)
                if clean.startswith("bestmove "):
                    parts = clean.split()
                    bestmove = parts[1] if len(parts) > 1 else ""
                    if len(parts) >= 4 and parts[2] == "ponder":
                        ponder = parts[3]
                    break
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            chosen_depth = 0
            chosen_bucket: dict[int, tuple[AnalysisLine, str | None]] = {}
            requested_count = max(1, int(multipv))
            complete_depths = [
                depth for depth, bucket in iterations.items()
                if all(index in bucket and bucket[index][1] is None
                        for index in range(1, requested_count + 1))
            ]
            exact_primary_depths = [
                depth for depth, bucket in iterations.items()
                if 1 in bucket
                and bucket[1][1] is None
            ]
            primary_depths = [
                depth for depth, bucket in iterations.items() if 1 in bucket
            ]
            # The executable score/PV must come from the latest completed
            # iteration, even when the engine's trailing bestmove disagrees.
            # That disagreement is retained below as an explicit trust failure
            # so automatic play can verify and then fall back to this PV.
            if complete_depths:
                chosen_depth = max(complete_depths)
            elif exact_primary_depths:
                chosen_depth = max(exact_primary_depths)
            elif primary_depths:
                chosen_depth = max(primary_depths)
            elif iterations:
                chosen_depth = max(iterations)
            if chosen_depth:
                chosen_bucket = iterations[chosen_depth]
            lines = [chosen_bucket[key][0] for key in sorted(chosen_bucket)]
            exact_bucket = bool(chosen_bucket) and all(bound is None for _, bound in chosen_bucket.values())
            best_matches = bool(lines) and lines[0].best_move == bestmove
            requested_complete = len(lines) >= requested_count
            trusted = bool(bestmove and bestmove != "(none)" and best_matches and exact_bucket
                           and (requested_complete or int(multipv) == 1))
            if trusted:
                trust_reason = "complete_iteration"
            elif not exact_bucket:
                trust_reason = "bound_or_partial_iteration"
            elif not best_matches:
                trust_reason = "bestmove_pv_mismatch"
            else:
                trust_reason = "incomplete_multipv_iteration"

            stability = 0
            for depth in sorted(iterations, reverse=True):
                bucket = iterations[depth]
                if 1 not in bucket or bucket[1][1] is not None:
                    continue
                if bucket[1][0].best_move != bestmove:
                    break
                stability += 1

            elapsed_ms = (time.monotonic() - started) * 1000
            result = EngineSearchResult(
                lines,
                bestmove,
                ponder,
                chosen_depth,
                stability,
                trusted,
                trust_reason,
                elapsed_ms,
            )
            if lines:
                top = lines[0]
                LOGGER.info(
                    "analysis done bestmove=%s ponder=%s depth=%s score=%s:%s wdl=%s "
                    "stability=%s trusted=%s trust_reason=%s elapsed_ms=%.1f budget_ms=%s",
                    bestmove,
                    ponder,
                    top.depth,
                    top.score_type,
                    top.score,
                    top.wdl,
                    stability,
                    trusted,
                    trust_reason,
                    elapsed_ms,
                    movetime_ms,
                )
            else:
                LOGGER.info("analysis done bestmove=%s no analysis lines", bestmove)
            return result

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                self._send("stop")
            except (BrokenPipeError, EngineError):
                pass

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            process = self.process
            self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    with self._write_lock:
                        if process.stdin:
                            process.stdin.write("quit\n")
                            process.stdin.flush()
                    process.wait(timeout=1.5)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.5)
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
