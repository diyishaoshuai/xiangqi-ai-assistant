from __future__ import annotations

import os
import logging
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from core import AnalysisLine


INFO_RE = re.compile(
    r"\bdepth (?P<depth>\d+).*?\bmultipv (?P<multipv>\d+).*?"
    r"\bscore (?P<score_type>cp|mate) (?P<score>-?\d+).*?\bpv (?P<pv>.+)$"
)
WDL_RE = re.compile(r"\bwdl (?P<win>\d+) (?P<draw>\d+) (?P<loss>\d+)\b")
LOGGER = logging.getLogger("xiangqi_ai.engine")


class EngineError(RuntimeError):
    pass


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
        cancelled=None,
    ) -> tuple[list[AnalysisLine], str]:
        with self._search_lock:
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            self.start()
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            started = time.monotonic()
            move_history = list(moves or ())
            LOGGER.info(
                "analysis start fen=%s history_fen=%s moves=%s movetime_ms=%s multipv=%s",
                fen,
                history_fen or fen,
                " ".join(move_history) or "-",
                movetime_ms,
                multipv,
            )
            self._send(f"setoption name MultiPV value {multipv}")
            position_command = f"position fen {history_fen or fen}"
            if move_history:
                position_command += " moves " + " ".join(move_history)
            self._send(position_command)
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            self._send(f"go movetime {movetime_ms}")
            # Cover stop arriving immediately before/while sending 'go'. Drain
            # that search's bestmove before releasing the shared engine lock.
            stop_sent = cancelled is not None and cancelled()
            if stop_sent:
                self._send("stop")
            process = self.process
            if not process or not process.stdout:
                raise EngineError("引擎尚未启动")
            latest: dict[int, AnalysisLine] = {}
            bestmove = ""
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
                    latest[item.multipv] = item
                if clean.startswith("bestmove "):
                    parts = clean.split()
                    bestmove = parts[1] if len(parts) > 1 else ""
                    break
            if cancelled is not None and cancelled():
                raise InterruptedError("搜索已取消")
            result = [latest[key] for key in sorted(latest)]
            if result:
                top = result[0]
                LOGGER.info(
                    "analysis done bestmove=%s depth=%s score=%s:%s wdl=%s elapsed_ms=%.1f budget_ms=%s",
                    bestmove,
                    top.depth,
                    top.score_type,
                    top.score,
                    top.wdl,
                    (time.monotonic() - started) * 1000,
                    movetime_ms,
                )
            else:
                LOGGER.info("analysis done bestmove=%s no analysis lines", bestmove)
            return result, bestmove

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
