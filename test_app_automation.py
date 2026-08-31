import threading
import queue
import unittest
from unittest.mock import Mock, patch

from app import XiangqiApp
from automation import AutomationState, click_screen_move
from core import START_FEN, AnalysisLine, apply_move, parse_fen, parse_move
from recognition import BoardGeometry
from test_automation import FakeUser32


class CaptureHarness:
    def __init__(self, frames):
        self.frames = list(frames)
        self.statuses = []
        self.recoveries = []
        self.sleep_count = 0

    def _mouse_autoplay_cancelled(self, _session_id, _stop_event):
        return False

    def _capture_mouse_board(
        self,
        _session_id,
        _stop_event,
        *,
        allow_terminal=False,
    ):
        del allow_terminal
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame

    def _queue_mouse_status(self, session_id, state, status):
        self.statuses.append((session_id, state, status))

    def _mouse_sleep(self, _session_id, _stop_event, _seconds):
        self.sleep_count += 1

    def _log_mouse_recovery(self, session_id, reason):
        self.recoveries.append((session_id, reason))


class ClickReadyHarness:
    def __init__(self, stable_result):
        self.stable_result = stable_result
        self.statuses = []
        self.recoveries = []
        self.sleep_count = 0

    def _queue_mouse_status(self, session_id, state, status):
        self.statuses.append((session_id, state, status))

    def _mouse_sleep(self, _session_id, _stop_event, _seconds):
        self.sleep_count += 1

    def _capture_stable_mouse_board(self, *_args, **_kwargs):
        return self.stable_result

    def _capture_unchanged_click_board(self, *_args):
        return None

    def _log_mouse_recovery(self, session_id, reason):
        self.recoveries.append((session_id, reason))

    _known_new_game_board = staticmethod(XiangqiApp._known_new_game_board)


class AutoplayFlowTests(unittest.TestCase):
    def test_capture_recovers_after_errors_and_uses_latest_geometry(self):
        board = {(4, 9): "k", (4, 0): "K"}
        ambiguous = {(3, 9): "k", (4, 0): "K"}
        first_geometry = object()
        latest_geometry = object()
        harness = CaptureHarness(
            [
                RuntimeError("低置信度格子"),
                (board, "grid-1", first_geometry),
                (board, "grid-2", first_geometry),
                (ambiguous, "grid-x", object()),
                (board, "grid-3", first_geometry),
                (board, "grid-4", first_geometry),
                (board, "grid-5", latest_geometry),
            ]
        )
        result = XiangqiApp._capture_stable_mouse_board(
            harness,
            7,
            threading.Event(),
            stable_frames=3,
            accept=lambda candidate: candidate == board,
            state=AutomationState.WAITING_BOARD,
        )
        self.assertEqual(result[0], board)
        self.assertEqual(result[1], "grid-5")
        self.assertIs(result[2], latest_geometry)
        self.assertTrue(harness.statuses)
        self.assertEqual(harness.recoveries[-1][0], 7)

    def test_click_waits_for_foreground_and_user_idle_then_recovers(self):
        board = {(4, 9): "k", (4, 0): "K"}
        geometry = object()
        harness = ClickReadyHarness((board, "grid", geometry))
        with (
            patch(
                "app.foreground_window",
                side_effect=[99, 42, 42, 42],
            ),
            patch(
                "app.user_input_is_idle",
                side_effect=[False, True, True],
            ),
        ):
            kind, result = XiangqiApp._wait_for_click_ready(
                harness,
                8,
                threading.Event(),
                42,
                board,
                board,
                {},
            )
        self.assertEqual(kind, "ready")
        self.assertIs(result[2], geometry)
        self.assertEqual(harness.sleep_count, 2)
        self.assertEqual(harness.recoveries, [(8, "用户输入")])
        states = [state for _, state, _ in harness.statuses]
        self.assertIn(AutomationState.WAITING_BOARD, states)
        self.assertIn(AutomationState.WAITING_USER_IDLE, states)


class AutoplayLifecycleTests(unittest.TestCase):
    def make_app(self):
        app = object.__new__(XiangqiApp)
        app.closing = False
        app.mouse_auto_running = True
        app.mouse_auto_session_id = 7
        app.mouse_auto_state = AutomationState.ACQUIRING
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_pending_start = False
        app.mouse_auto_last_status = None
        app.mouse_auto_thread = Mock()
        app.mouse_auto_button = Mock()
        app.root = Mock()
        app.always_on_top_var = Mock(get=Mock(return_value=True))
        app.status_var = Mock()
        app.engine = Mock()
        app.logger = Mock()
        app.result_queue = queue.Queue()
        app.auto_analysis_var = Mock(get=Mock(return_value=False))
        app.mouse_hotkey_queue = queue.Queue()
        app.global_hotkey = Mock()
        app.global_hotkey.thread.is_alive.return_value = True
        return app

    def test_stop_restores_window_before_waiting_for_worker_or_engine(self):
        app = self.make_app()
        app.engine.stop.side_effect = lambda: app.root.deiconify.assert_called()
        app._request_mouse_autoplay_stop("F1 急停")
        self.assertTrue(app.mouse_auto_stop_event.is_set())
        self.assertEqual(app.mouse_auto_state, AutomationState.STOPPING)
        app.root.deiconify.assert_called()

    def test_repeated_f1_while_stopping_never_queues_a_restart(self):
        app = self.make_app()
        app.mouse_auto_state = AutomationState.STOPPING
        app.mouse_auto_stop_event.set()
        app._toggle_mouse_autoplay()
        app._toggle_mouse_autoplay()
        self.assertFalse(app.mouse_auto_pending_start)
        app.root.deiconify.assert_called()

    def test_worker_normal_cancelled_loop_exit_also_reports_done(self):
        app = self.make_app()
        board, _ = parse_fen(START_FEN)
        app._mouse_sleep = Mock()
        app._capture_stable_mouse_board = Mock(return_value=(board, None, object()))
        app._window_for_geometry = Mock(return_value=42)
        app._queue_mouse_board = Mock(side_effect=lambda *_: app.mouse_auto_stop_event.set())
        with patch("app.foreground_window", return_value=42):
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        messages = list(app.result_queue.queue)
        done = [payload for kind, payload in messages if kind == "mouse_done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][:2], (7, "silent"))

    def test_duplicate_completion_cannot_restore_a_new_or_finished_session(self):
        app = self.make_app()
        app.mouse_auto_running = False
        app.mouse_auto_state = AutomationState.IDLE
        app._finish_mouse_autoplay(7, "silent", "已停止", "已停止")
        app.root.deiconify.assert_not_called()

    def test_background_hotkey_cancels_without_waiting_for_tk(self):
        app = self.make_app()
        app._on_global_f1()
        self.assertTrue(app.mouse_auto_stop_event.is_set())
        app.root.deiconify.assert_not_called()  # No Tk calls from listener.
        app._poll_f1_hotkey()
        app.root.deiconify.assert_called()
        self.assertEqual(app.mouse_auto_state, AutomationState.STOPPING)

    def test_stale_hotkey_cannot_stop_a_new_session(self):
        app = self.make_app()
        app.mouse_hotkey_queue.put(("stop", 6))
        app._poll_f1_hotkey()
        self.assertFalse(app.mouse_auto_stop_event.is_set())

    def test_hotkey_dispatch_exception_does_not_disable_next_poll(self):
        app = self.make_app()
        app.mouse_hotkey_queue.put(("stop", 7))
        app._request_mouse_autoplay_stop = Mock(side_effect=RuntimeError("test"))
        app._poll_f1_hotkey()
        app.root.after.assert_called_once_with(50, app._poll_f1_hotkey)

    def test_recognized_board_reaches_mouse_transaction_and_confirmation(self):
        app = self.make_app()
        board, _ = parse_fen("9/3k2C2/3a2N2/9/3PPR3/3r5/3N5/3p5/2r1p4/3K5 w - - 0 1")
        move = "g7e6"  # Actual recommendation from the user's failure log.
        expected = apply_move(board, move)
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500))
        app._capture_mouse_board = Mock(side_effect=[(board, None, geometry)] * 6 + [(expected, None, geometry)] * 2)
        app._mouse_sleep = Mock()
        app._window_for_geometry = Mock(return_value=42)
        app.engine.analyse.return_value = ([AnalysisLine(1, 40, "mate", 12, [move], (1000, 0, 0))], move)
        original_queue_board = app._queue_mouse_board

        def queue_board(*args):
            original_queue_board(*args)
            if args[1] == expected:
                app.mouse_auto_stop_event.set()

        app._queue_mouse_board = queue_board
        cursor = FakeUser32([True, True])

        def simulated_click(start, end, cancelled, **kwargs):
            kwargs.pop("pause_seconds", None)
            return click_screen_move(start, end, cancelled, pause_seconds=0,
                                     user32=cursor, sleep=lambda _: None, **kwargs)

        with patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True), patch("app.click_screen_move", side_effect=simulated_click) as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        click.assert_called_once()
        start, end = parse_move(move)
        self.assertEqual(click.call_args.args[:2], (geometry.point_for_square(start), geometry.point_for_square(end)))
        self.assertEqual(len(cursor.mouse_events), 4)
        messages = list(app.result_queue.queue)
        self.assertEqual([payload[1] for kind, payload in messages if kind == "mouse_board"][-1], expected)
        self.assertEqual([payload[1] for kind, payload in messages if kind == "mouse_done"], ["silent"])

    def test_cancelled_capture_is_not_swallowed_as_recognition_failure(self):
        harness = CaptureHarness([InterruptedError("stop")])
        with self.assertRaises(InterruptedError):
            XiangqiApp._capture_stable_mouse_board(harness, 7, threading.Event())
        self.assertEqual(harness.sleep_count, 0)

    def test_fast_opponent_reply_search_overlaps_confirmation_but_cannot_click_early(self):
        app = self.make_app()
        initial, _ = parse_fen(START_FEN)
        first_move, opponent, second_move = "c3c4", "h7c7", "c0e2"
        expected = apply_move(initial, first_move)
        reply = apply_move(expected, opponent)
        final = apply_move(reply, second_move)
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500))
        app._mouse_sleep = Mock()
        app._window_for_geometry = Mock(return_value=42)
        search_started, allow_result = threading.Event(), threading.Event()
        vision_confirmed = False

        def analyse(*args, **kwargs):
            move = first_move
            if kwargs.get("moves"):
                self.assertEqual(tuple(kwargs["moves"]), (first_move, opponent))
                search_started.set()
                self.assertTrue(allow_result.wait(1))
                move = second_move
            return [AnalysisLine(1, 20, "cp", 100, [move], (500, 500, 0))], move

        app.engine.analyse.side_effect = analyse
        captures = 0

        def capture(*args, **kwargs):
            nonlocal captures, vision_confirmed
            captures += 1
            if captures in (1, 2):
                return initial, None, geometry
            if captures == 3:
                self.assertFalse(kwargs["accept"](initial))  # No immediate blind retry.
                self.assertTrue(kwargs["accept"](reply))
                kwargs["on_candidate"](reply)  # First frame starts search.
                self.assertTrue(search_started.wait(1))
                self.assertEqual(click.call_count, 1)  # Only the PREVIOUS move clicked.
                kwargs["on_candidate"](reply)  # Second independent frame agrees.
                vision_confirmed = True
                allow_result.set()
                return reply, None, geometry
            if captures == 4:
                return reply, None, geometry
            return final, None, geometry

        app._capture_stable_mouse_board = capture
        queue_board = app._queue_mouse_board

        def publish(*args):
            queue_board(*args)
            if args[1] == final:
                app.mouse_auto_stop_event.set()

        app._queue_mouse_board = publish

        def send_move(*args, **kwargs):
            if click.call_count == 2:
                self.assertTrue(vision_confirmed)
            self.assertEqual(kwargs["pause_seconds"], .10)
            self.assertEqual(kwargs["settle_seconds"], .03)
            from automation import ClickResult
            return ClickResult(True, True, 2)

        with patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True), patch("app.click_screen_move", side_effect=send_move) as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(app.engine.analyse.call_count, 2)  # No second 500ms search after confirming.
        self.assertEqual([payload[1] for kind, payload in app.result_queue.queue if kind == "mouse_done"], ["silent"])


if __name__ == "__main__":
    unittest.main()
