import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app import XiangqiApp
from automation import classify_board_transition, TransitionKind
from core import AnalysisLine, parse_fen
from test_app_automation import CaptureHarness
from winrate import WinrateChart, WinrateHistory, red_probabilities


class WinrateTests(unittest.TestCase):
    def line(self, wdl=(700, 200, 100)):
        return AnalysisLine(1, 20, "cp", 100, ["a0a1"], wdl)

    def test_red_perspective_and_missing_data(self):
        self.assertEqual(red_probabilities(self.line(), "w"), (70, 20, 10))
        self.assertEqual(red_probabilities(self.line(), "b"), (10, 20, 70))
        for wdl in (None, (0, 0, 0), (-1, 1, 1000)):
            self.assertIsNone(red_probabilities(self.line(wdl), "w"))

    def test_repeat_update_undo_branch_and_new_root(self):
        history = WinrateHistory()
        history.record("root", [], self.line(), "w")
        history.record("root", ["a"], self.line(), "b")
        history.record("root", ["a"], self.line(), "b")
        self.assertEqual(len(history.points), 2)
        history.record("root", ["a", "b"], self.line(), "w")
        history.sync("root", ["a", "c"])
        self.assertEqual(set(history.points), {0, 1})
        history.sync("root", [])
        self.assertEqual(set(history.points), {0})
        history.sync("new", [])
        self.assertEqual(history.points, {})

    def test_canvas_renders_empty_single_point_and_curve_without_game_input(self):
        root = tk.Tk()
        root.withdraw()
        try:
            chart = WinrateChart(root)
            chart.winfo_width = lambda: 300
            chart.winfo_height = lambda: 128
            chart.redraw()
            self.assertTrue(any("等待" in chart.itemcget(item, "text")
                                for item in chart.find_all() if chart.type(item) == "text"))
            chart.history.record("root", [], self.line(), "w")
            chart.history.record("root", ["a"], self.line(), "b")
            chart.redraw()
            self.assertEqual(sum(chart.type(item) == "oval" for item in chart.find_all()), 2)
            self.assertEqual(sum(chart.type(item) == "line" for item in chart.find_all()), 4)
            self.assertTrue(any("红胜 10.0%" in chart.itemcget(item, "text")
                                for item in chart.find_all() if chart.type(item) == "text"))
        finally:
            root.destroy()


class OpponentRecoveryTests(unittest.TestCase):
    def test_actual_jj_pawn_move_is_legal(self):
        before, side = parse_fen("1R1aka3/9/9/p7p/3P5/4c3P/P8/4p1r2/1CN6/2BK1AN2 b - - 0 1")
        after, _ = parse_fen("1R1aka3/9/9/p7p/3P5/4c3P/P8/3p2r2/1CN6/2BK1AN2 w - - 0 1")
        result = classify_board_transition(before, after, side)
        self.assertEqual(result.kind, TransitionKind.MOVE)
        self.assertEqual(result.move, "e2d2")

    def test_rejection_evicts_cache_even_without_tracking_mode(self):
        harness = CaptureHarness([({(0, 0): "R"}, None, object())])
        harness.mouse_auto_frame_cache = Mock()
        with self.assertRaises(TimeoutError):
            XiangqiApp._capture_stable_mouse_board(
                harness, 1, threading.Event(), accept=lambda _: False, max_attempts=1)
        harness.mouse_auto_frame_cache.clear.assert_called_once()

    def test_opponent_timeout_relocks_without_transaction_and_remains_cancellable(self):
        harness = CaptureHarness([({(0, 0): "R"}, None, object())] * 10)
        harness.mouse_auto_geometry = object()
        harness.mouse_auto_frame_cache = Mock()
        harness.logger = Mock()
        clock = iter(range(100))
        with patch("app.time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                XiangqiApp._capture_stable_mouse_board(
                    harness, 1, threading.Event(), opponent_anchor={(1, 0): "R"},
                    accept=lambda _: False, max_attempts=10)
        self.assertIsNone(harness.mouse_auto_geometry)
        self.assertTrue(any("棋盘同步异常" in entry[2] for entry in harness.statuses))
        harness._mouse_autoplay_cancelled = lambda *_: True
        with self.assertRaises(InterruptedError):
            XiangqiApp._capture_stable_mouse_board(harness, 1, threading.Event())


if __name__ == "__main__":
    unittest.main()
