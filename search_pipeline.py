"""Search a candidate while vision confirms it; only an exact confirmed key can consume it."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
import threading
import time


class ConfirmedSearch:
    def __init__(self, engine):
        self.engine = engine
        self.key = self.future = self.thread = None
        self.cancelled = threading.Event()
        self.started = self.finished = None

    @staticmethod
    def request_key(fen, budget, multipv, history_fen, moves):
        return fen, budget, multipv, history_fen, tuple(moves)

    def offer(self, key):
        if key == self.key and not self.cancelled.is_set():
            return False
        self.cancel()
        if self.thread is not None and self.thread.is_alive():
            return False  # Never queue stale boards or concurrent searches.
        self.key, self.future = key, Future()
        self.cancelled = threading.Event()
        future, cancel = self.future, self.cancelled
        self.started, self.finished = time.perf_counter(), None

        def run():
            try:
                fen, budget, multipv, history, moves = key
                result = self.engine.analyse(fen, budget, multipv, history_fen=history,
                                             moves=moves, cancelled=cancel.is_set)
                if cancel.is_set():
                    raise InterruptedError("候选局面已失效")
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self.finished = time.perf_counter()

        self.thread = threading.Thread(target=run, daemon=True, name="confirmed-search")
        self.thread.start()
        return True

    def take(self, confirmed_key, cancelled):
        if cancelled():
            self.cancel()
            raise InterruptedError("用户已停止自动接管")
        if self.key != confirmed_key or self.cancelled.is_set() or self.future is None:
            self.cancel()
            return None
        while True:
            if cancelled():
                self.cancel()
                raise InterruptedError("用户已停止自动接管")
            try:
                result = self.future.result(timeout=.02)
                if cancelled():
                    self.cancel()
                    raise InterruptedError("用户已停止自动接管")
                return result
            except TimeoutError:
                continue

    def cancel(self):
        if not self.cancelled.is_set():
            self.cancelled.set()
            if self.thread is not None and self.thread.is_alive():
                self.engine.stop()

    def close(self):
        self.cancel()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
