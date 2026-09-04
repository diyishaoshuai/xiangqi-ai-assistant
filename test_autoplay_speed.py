"""Latency-path regressions; all screenshots/input are local fixtures or mocks."""
import io
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app import XiangqiApp, bounded_search_settings
from automation import AutomationState
from engine import PikafishEngine
from recognition import BoardGeometry
from screen_cache import UnchangedBoardCache


class PixelProofTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (800, 600), (20, 30, 40))
        self.geometry = BoardGeometry((1, 0, 100, 0, 1, 30, 0, 0, 1), False, .9, self.image.size)
        self.board = {(4, 0): "K", (4, 9): "k", (0, 0): "R"}
        self.cache = UnchangedBoardCache()
        self.cache.remember(self.image, self.board, "grid", self.geometry)

    def test_identical_frame_reuses_result_but_not_mutable_board(self):
        current = self.cache.match(self.image.copy(), expected_board=self.board)
        self.assertEqual(current, (self.board, "grid", self.geometry))
        current[0].clear()
        self.assertEqual(self.cache.match(self.image)[0], self.board)

    def test_every_square_and_outer_rim_is_guarded_even_for_one_changed_pixel(self):
        for x in range(9):
            for rank in range(10):
                px, py = self.geometry.point_for_square((x, rank))
                for offset in (0, -20, 20):
                    image = self.image.copy()
                    image.putpixel((round(px) + offset, round(py)), (21, 30, 40))
                    self.assertIsNone(self.cache.match(image), (x, rank, offset))

    def test_timer_outside_board_does_not_force_reclassification(self):
        image = self.image.copy()
        image.putpixel((799, 10), (255, 255, 255))
        self.assertIsNotNone(self.cache.match(image))

    def test_resolution_board_mismatch_and_clear_invalidate_proof(self):
        self.assertIsNone(self.cache.match(self.image.resize((1600, 1200))))
        self.assertIsNone(self.cache.match(self.image, expected_board={(4, 0): "K"}))
        self.cache.clear()
        self.assertIsNone(self.cache.match(self.image))

    def make_app(self):
        app = object.__new__(XiangqiApp)
        app.closing = False
        app.mouse_auto_session_id = 7
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_frame_cache = self.cache
        app.logger = Mock()
        app.recognizer = Mock()
        app.recognizer.refresh_geometry.side_effect = RuntimeError("fast pose unavailable")
        app.mouse_auto_geometry = self.geometry
        app._capture_stable_mouse_board = Mock(return_value=(self.board, "new grid", self.geometry))
        app._queue_mouse_status = Mock()
        app._mouse_sleep = Mock()
        app._log_mouse_recovery = Mock()
        return app

    def test_after_thinking_unchanged_board_click_gate_needs_one_new_screenshot_no_model_or_sleep(self):
        app = self.make_app()
        with patch("app.ImageGrab.grab", return_value=self.image.copy()) as grab, patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True):
            kind, current = app._wait_for_click_ready(7, app.mouse_auto_stop_event, 42, self.board, {}, {})
        self.assertEqual(kind, "ready")
        self.assertEqual(current[0], self.board)
        grab.assert_called_once()
        app._capture_stable_mouse_board.assert_not_called()
        app.recognizer.recognize.assert_not_called()
        app._mouse_sleep.assert_not_called()

    def test_changed_board_requires_full_three_frame_revalidation(self):
        app = self.make_app()
        image = self.image.copy()
        image.putpixel((300, 300), (0, 0, 0))
        with patch("app.ImageGrab.grab", return_value=image), patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True):
            app._wait_for_click_ready(7, app.mouse_auto_stop_event, 42, self.board, {}, {})
        app._capture_stable_mouse_board.assert_called_once()
        self.assertEqual(app._capture_stable_mouse_board.call_args.kwargs["stable_frames"], 3)

    def test_changed_pixels_use_pose_only_before_full_classifier_fallback(self):
        app = self.make_app()
        image = self.image.copy()
        image.putpixel((300, 300), (0, 0, 0))
        refreshed = BoardGeometry(
            (1, 0, 101, 0, 1, 30, 0, 0, 1),
            False,
            .91,
            self.image.size,
            ((0, 0, 151.0, 480.0),),
        )
        app.recognizer.refresh_geometry.side_effect = None
        app.recognizer.refresh_geometry.return_value = refreshed
        with patch("app.ImageGrab.grab", return_value=image), patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True):
            kind, current = app._wait_for_click_ready(
                7, app.mouse_auto_stop_event, 42, self.board, {}, {},
            )
        self.assertEqual(kind, "ready")
        self.assertIs(current[2], refreshed)
        app.recognizer.refresh_geometry.assert_called_once()
        app._capture_stable_mouse_board.assert_not_called()

    def test_cancel_arriving_during_fast_screenshot_never_returns_click_ready(self):
        app = self.make_app()

        def grab():
            app.mouse_auto_stop_event.set()
            return self.image

        with patch("app.ImageGrab.grab", side_effect=grab), self.assertRaises(InterruptedError):
            app._capture_unchanged_click_board(7, app.mouse_auto_stop_event, self.board)

    def test_user_activity_is_not_bypassed_by_fast_pixel_proof(self):
        app = self.make_app()
        app._mouse_sleep.side_effect = InterruptedError("test stop")
        with patch("app.ImageGrab.grab") as grab, patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=False), self.assertRaises(InterruptedError):
            app._wait_for_click_ready(7, app.mouse_auto_stop_event, 42, self.board, {}, {})
        grab.assert_not_called()
        self.assertEqual(app._queue_mouse_status.call_args.args[1], AutomationState.WAITING_USER_IDLE)

    def test_fresh_consecutive_screenshots_do_not_rerun_onnx_when_pixels_match(self):
        app = self.make_app()
        with patch("app.ImageGrab.grab", side_effect=[self.image.copy(), self.image.copy()]) as grab:
            first = app._capture_mouse_board(7, app.mouse_auto_stop_event)
            second = app._capture_mouse_board(7, app.mouse_auto_stop_event)
        self.assertEqual(first, second)
        self.assertEqual(grab.call_count, 2)
        app.recognizer.recognize.assert_not_called()


class TimeBudgetTests(unittest.TestCase):
    def test_repeat_and_check_policies_never_extend_the_requested_budget(self):
        for budget in (100, 500, 1000, 3000, 30000):
            for repeating in (False, True):
                for checking in (False, True):
                    time_ms, candidates = bounded_search_settings(budget, 1, repeating=repeating, checking=checking)
                    self.assertEqual(time_ms, budget)
                    self.assertEqual(candidates, 12 if checking else 5 if repeating else 1)

    def test_engine_parses_all_pvs_without_flushing_thousands_of_depth_logs(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.start = Mock()
        engine.process = Mock()
        engine.process.stdout = io.StringIO("".join(
            f"info depth {i} multipv 1 score cp 100 pv a0a1\n" for i in range(1, 3001)
        ) + "bestmove a0a1\n")
        engine.process.stdin = io.StringIO()
        with patch("engine.time.monotonic", return_value=1.0), patch("engine.LOGGER") as logger:
            lines, best = engine.analyse("test", 500, 1)
        self.assertEqual(best, "a0a1")
        self.assertEqual(lines[0].depth, 3000)
        self.assertLessEqual(logger.debug.call_count, 2)
        self.assertIn("go movetime 500", engine.process.stdin.getvalue())

    def test_engine_uses_completed_iteration_matching_bestmove(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.start = Mock()
        engine.process = Mock()
        engine.process.stdout = io.StringIO(
            "info depth 10 multipv 1 score cp -450 pv d8c8 a7c8\n"
            "info depth 11 multipv 1 score cp -460 pv d8c8 a7c8\n"
            "info depth 12 multipv 1 score cp -469 pv d8c8 a7c8\n"
            "info depth 13 multipv 1 score cp -440 upperbound pv a6a5\n"
            "bestmove d8c8 ponder a7c8\n"
        )
        engine.process.stdin = io.StringIO()
        search = engine.analyse("test", 500, 1)
        self.assertEqual(search.bestmove, "d8c8")
        self.assertEqual(search.ponder, "a7c8")
        self.assertEqual(search.completed_depth, 12)
        self.assertEqual(search.lines[0].best_move, "d8c8")
        self.assertTrue(search.trusted)
        self.assertEqual(search.root_stability, 3)

    def test_engine_rejects_mixed_or_incomplete_multipv_depth(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.start = Mock()
        engine.process = Mock()
        engine.process.stdout = io.StringIO(
            "info depth 10 multipv 1 score cp 50 pv a0a1\n"
            "info depth 10 multipv 2 score cp 40 pv a0a2\n"
            "info depth 11 multipv 1 score cp 55 pv a0a1\n"
            "bestmove a0a1\n"
        )
        engine.process.stdin = io.StringIO()
        search = engine.analyse("test", 500, 2)
        self.assertEqual(search.completed_depth, 10)
        self.assertEqual([line.best_move for line in search.lines], ["a0a1", "a0a2"])
        self.assertTrue(search.trusted)

    def test_final_bestmove_mismatch_uses_latest_complete_pv_and_is_untrusted(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.start = Mock()
        engine.process = Mock()
        engine.process.stdout = io.StringIO(
            "info depth 11 multipv 1 score cp 30 pv a0a1\n"
            "info depth 12 multipv 1 score cp 45 pv a0a2\n"
            "bestmove a0a1\n"
        )
        engine.process.stdin = io.StringIO()
        search = engine.analyse("test", 500, 1)
        self.assertEqual(search.completed_depth, 12)
        self.assertEqual(search.lines[0].best_move, "a0a2")
        self.assertEqual(search.bestmove, "a0a1")
        self.assertFalse(search.trusted)
        self.assertEqual(search.trust_reason, "bestmove_pv_mismatch")

    def test_searchmoves_is_forwarded_to_uci(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.start = Mock()
        engine.process = Mock()
        engine.process.stdout = io.StringIO(
            "info depth 10 multipv 1 score cp 10 pv a0a1\n"
            "bestmove a0a1\n"
        )
        engine.process.stdin = io.StringIO()
        engine.analyse("test", 500, 1, root_moves=["a0a1", "a0a2"])
        self.assertIn(
            "go movetime 500 searchmoves a0a1 a0a2",
            engine.process.stdin.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
