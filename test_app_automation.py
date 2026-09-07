import threading
import queue
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from app import XiangqiApp
from automation import AutomationState, ClickResult, click_screen_move
from core import START_FEN, AnalysisLine, apply_move, parse_fen, parse_move
from recognition import BoardGeometry, Detection
from test_automation import FakeUser32
from tracking import MoveTransaction, TransactionState


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
        image=None,
        tracking_only=False,
        required_squares=None,
        allow_partial=False,
    ):
        del allow_terminal, image, tracking_only, required_squares, allow_partial
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
    @staticmethod
    def _endpoint_harness(frames):
        harness = CaptureHarness(frames)
        harness.logger = Mock()
        return harness

    def test_endpoint_confirmation_ignores_unrelated_missing_pieces(self):
        before = {(4, 9): "k", (4, 0): "K", (2, 4): "R", (7, 7): "p"}
        expected = apply_move(before, "c4c5")
        # This intentionally contains neither king and omits the unrelated
        # pawn: it represents a partial classifier frame, not a legal board.
        partial = {(2, 5): "R"}
        geometry = object()
        harness = self._endpoint_harness(
            [(partial, "grid-1", geometry), (partial, "grid-2", geometry)]
        )
        result = XiangqiApp._capture_click_endpoints(
            harness,
            7,
            threading.Event(),
            before,
            expected,
            (2, 4),
            (2, 5),
            "b",
        )
        self.assertEqual(result, (expected, "grid-2", geometry))
        self.assertEqual(harness.sleep_count, 1)

    def test_endpoint_confirmation_never_authorizes_a_second_click(self):
        before = {(4, 9): "k", (4, 0): "K", (2, 4): "R"}
        expected = apply_move(before, "c4c5")
        partial = {(2, 4): "R"}
        geometry = object()
        harness = self._endpoint_harness([(partial, None, geometry)] * 3)
        harness.mouse_auto_transaction = MoveTransaction("c4c5", before, expected)
        harness.mouse_auto_transaction.destination_sent()
        harness.mouse_auto_transaction.observe()
        result = XiangqiApp._capture_click_endpoints(
            harness,
            7,
            threading.Event(),
            before,
            expected,
            (2, 4),
            (2, 5),
            "b",
            max_attempts=3,
        )
        self.assertIsNone(result)
        self.assertEqual(harness.sleep_count, 2)
        self.assertEqual(harness.mouse_auto_transaction.destination_send_count, 1)
        self.assertEqual(harness.mouse_auto_transaction.state, TransactionState.UNCERTAIN)

    def test_partial_capture_accepts_unrelated_unknown_but_not_endpoint_unknown(self):
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        known = Detection((2, 5), "w", "R", .99, .8, Image.new("RGB", (1, 1)), [])
        unrelated_unknown = Detection((7, 7), "b", None, .2, .01, Image.new("RGB", (1, 1)), [])
        app = object.__new__(XiangqiApp)
        app.mouse_auto_frame_cache = None
        app.mouse_auto_detect_side = False
        app._mouse_autoplay_cancelled = Mock(return_value=False)
        app.logger = Mock()
        app.recognizer = Mock()
        app.recognizer.last_backend = "onnx"
        app.recognizer.last_geometry = geometry
        app.recognizer.recognize.return_value = ("grid", [known, unrelated_unknown])
        result = XiangqiApp._capture_mouse_board(
            app,
            7,
            threading.Event(),
            image=Image.new("RGB", (500, 500)),
            required_squares=((2, 4), (2, 5)),
            allow_partial=True,
        )
        self.assertEqual(result[0], {(2, 5): "R"})

        endpoint_unknown = Detection((2, 4), "w", None, .2, .01, Image.new("RGB", (1, 1)), [])
        app.recognizer.recognize.return_value = ("grid", [known, endpoint_unknown])
        with self.assertRaisesRegex(RuntimeError, "起点或终点"):
            XiangqiApp._capture_mouse_board(
                app,
                7,
                threading.Event(),
                image=Image.new("RGB", (500, 500)),
                required_squares=((2, 4), (2, 5)),
                allow_partial=True,
            )

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

    def test_capture_confirmation_has_a_bounded_wait(self):
        board = {(4, 9): "k", (4, 0): "K"}
        ambiguous = {(3, 9): "k", (4, 0): "K"}
        harness = CaptureHarness([(ambiguous, None, object())] * 8)
        clock = iter((0.0, 0.4, 1.0, 1.6, 2.2, 2.6, 3.0))
        with patch("app.time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                XiangqiApp._capture_stable_mouse_board(
                    harness,
                    7,
                    threading.Event(),
                    stable_frames=1,
                    accept=lambda candidate: candidate == board,
                    max_wait_seconds=2.4,
                )
        self.assertGreaterEqual(harness.sleep_count, 1)

    def test_rejected_tracking_result_clears_decoded_frame_cache(self):
        expected = {(4, 9): "k", (4, 0): "K"}
        wrong = {(3, 9): "k", (4, 0): "K"}
        harness = CaptureHarness(
            [(wrong, None, object()), (expected, None, object())]
        )
        harness.mouse_auto_frame_cache = Mock()
        result = XiangqiApp._capture_stable_mouse_board(
            harness,
            7,
            threading.Event(),
            stable_frames=1,
            accept=lambda candidate: candidate == expected,
            max_attempts=2,
            tracking_only=True,
        )
        self.assertEqual(result[0], expected)
        harness.mouse_auto_frame_cache.clear.assert_called_once_with()

    def test_animation_settle_uses_only_locked_board_pixels(self):
        harness = CaptureHarness([])
        harness.logger = Mock()
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        moving = Image.new("RGB", (500, 500), "black")
        settled = Image.new("RGB", (500, 500), "white")
        frames = [moving, settled, settled.copy(), settled.copy()]
        with patch("app.ImageGrab.grab", side_effect=frames):
            result = XiangqiApp._wait_for_board_animation_settle(
                harness,
                7,
                threading.Event(),
                geometry,
            )
        self.assertEqual(result.getpixel((250, 250)), (255, 255, 255))
        self.assertEqual(harness.sleep_count, 3)

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

    def test_click_retry_forces_full_relock_and_skips_cached_geometry(self):
        board = {(4, 9): "k", (4, 0): "K"}
        geometry = object()
        harness = ClickReadyHarness((board, "grid", geometry))
        harness._capture_unchanged_click_board = Mock()
        harness._capture_fast_click_board = Mock()
        harness._capture_stable_mouse_board = Mock(
            return_value=(board, "grid", geometry)
        )
        with (
            patch("app.foreground_window", return_value=42),
            patch("app.user_input_is_idle", return_value=True),
        ):
            kind, result = XiangqiApp._wait_for_click_ready(
                harness,
                8,
                threading.Event(),
                42,
                board,
                board,
                {},
                force_full_relock=True,
            )
        self.assertEqual(kind, "ready")
        self.assertIs(result[2], geometry)
        harness._capture_unchanged_click_board.assert_not_called()
        harness._capture_fast_click_board.assert_not_called()
        self.assertEqual(
            harness._capture_stable_mouse_board.call_args.kwargs["stable_frames"],
            2,
        )

    def test_click_retry_waits_for_full_board_after_selection_glow(self):
        board = {(4, 9): "k", (4, 0): "K", (7, 0): "N"}
        geometry = object()
        harness = ClickReadyHarness(None)
        harness._capture_unchanged_click_board = Mock()
        harness._capture_stable_mouse_board = Mock(
            side_effect=[TimeoutError("选中光效"), (board, "grid", geometry)]
        )
        harness._capture_fast_click_board = Mock(
            return_value=(board, "grid", geometry)
        )
        with (
            patch("app.foreground_window", return_value=42),
            patch("app.user_input_is_idle", return_value=True),
        ):
            kind, result = XiangqiApp._wait_for_click_ready(
                harness,
                8,
                threading.Event(),
                42,
                board,
                board,
                {},
                force_full_relock=True,
            )
        self.assertEqual(kind, "ready")
        self.assertEqual(result, (board, "grid", geometry))
        harness._capture_fast_click_board.assert_not_called()
        self.assertEqual(harness._capture_stable_mouse_board.call_count, 2)


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

    def test_restart_adopts_the_pending_clicked_position(self):
        app = self.make_app()
        anchor, _ = parse_fen(START_FEN)
        move = "g3g4"
        pending = apply_move(anchor, move)
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        app.mouse_auto_detect_side = False
        app.mouse_resume_pending_board = dict(pending)
        app._mouse_sleep = Mock()
        app._capture_stable_mouse_board = Mock(return_value=(pending, None, geometry))
        app._window_for_geometry = Mock(return_value=42)
        published = []

        def publish(*args):
            published.append(args)
            app.mouse_auto_stop_event.set()

        app._queue_mouse_board = publish
        resume_state = (
            anchor,
            "w",
            START_FEN,
            [],
            (pending, "b", START_FEN, [move]),
        )
        with patch("app.foreground_window", return_value=42):
            app._mouse_autoplay_worker(
                7,
                app.mouse_auto_stop_event,
                "w",
                "w",
                500,
                1,
                resume_state,
            )
        self.assertEqual(published[0][1], pending)
        self.assertEqual(published[0][2], "b")
        self.assertEqual(published[0][5], [move])
        self.assertIsNone(app.mouse_resume_pending_board)
        app.engine.analyse.assert_not_called()

    def test_restart_waits_for_pending_position_instead_of_retrying(self):
        app = self.make_app()
        anchor, _ = parse_fen(START_FEN)
        move = "g3g4"
        pending = apply_move(anchor, move)
        observed = dict(anchor)
        observed[(0, 0)] = "C"
        observed[(2, 0)] = "N"
        observed[(4, 9)] = "a"
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        app.mouse_auto_detect_side = False
        app.mouse_resume_pending_board = dict(pending)
        app._mouse_sleep = Mock()
        app._capture_stable_mouse_board = Mock(
            side_effect=[(observed, None, geometry), (pending, None, geometry)]
        )
        app._window_for_geometry = Mock(return_value=42)
        published = []

        def publish(*args):
            published.append(args)
            app.mouse_auto_stop_event.set()

        app._queue_mouse_board = publish
        resume_state = (
            anchor,
            "w",
            START_FEN,
            [],
            (pending, "b", START_FEN, [move]),
        )
        with patch("app.foreground_window", return_value=42):
            app._mouse_autoplay_worker(
                7,
                app.mouse_auto_stop_event,
                "w",
                "w",
                500,
                1,
                resume_state,
            )
        self.assertEqual(published[0][1], pending)
        self.assertEqual(published[0][2], "b")
        self.assertEqual(published[0][5], [move])
        self.assertIsNone(app.mouse_resume_pending_board)
        app.engine.analyse.assert_not_called()

    def test_restart_never_reclicks_when_persisted_transaction_still_shows_anchor(self):
        app = self.make_app()
        anchor, _ = parse_fen(START_FEN)
        move = "g3g4"
        pending = apply_move(anchor, move)
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        app.mouse_auto_detect_side = False
        app.mouse_resume_pending_board = dict(pending)
        app._mouse_sleep = Mock()
        app._capture_stable_mouse_board = Mock(
            side_effect=[(anchor, None, geometry), (pending, None, geometry)]
        )
        app._window_for_geometry = Mock(return_value=42)
        published = []

        def publish(*args):
            published.append(args)
            app.mouse_auto_stop_event.set()

        app._queue_mouse_board = publish
        resume_state = (
            anchor,
            "w",
            START_FEN,
            [],
            (pending, "b", START_FEN, [move]),
            "generic",
        )
        with patch("app.foreground_window", return_value=42), patch("app.click_screen_move") as click:
            app._mouse_autoplay_worker(
                7,
                app.mouse_auto_stop_event,
                "w",
                "w",
                500,
                1,
                resume_state,
            )
        click.assert_not_called()
        app.engine.analyse.assert_not_called()
        self.assertEqual(published[0][1], pending)

    @patch("app.messagebox.showerror")
    def test_takeover_failure_never_uses_modal_error_dialog(self, showerror):
        app = self.make_app()
        app._finish_mouse_autoplay(7, "error", "自动接管无法继续", "测试错误")
        showerror.assert_not_called()
        app.status_var.set.assert_called_with("测试错误")

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
        start, end = parse_move(move)
        projected_start = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        ).point_for_square(start)
        observed_start = (projected_start[0] + 7.0, projected_start[1] - 5.0)
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1),
            False,
            .8,
            (500, 500),
            ((start[0], start[1], *observed_start),),
        )
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
        self.assertEqual(
            click.call_args.args[:2],
            (geometry.point_for_piece(start), geometry.point_for_square(end)),
        )
        self.assertEqual(len(cursor.mouse_events), 4)
        messages = list(app.result_queue.queue)
        self.assertEqual([payload[1] for kind, payload in messages if kind == "mouse_board"][-1], expected)
        self.assertEqual([payload[1] for kind, payload in messages if kind == "mouse_done"], ["silent"])

    def test_takeover_keeps_playing_when_wdl_predicts_certain_loss(self):
        app = self.make_app()
        board, _ = parse_fen(
            "3rkabr1/4a4/1c2b4/p3p2Cp/2P4R1/4P4/"
            "P1c3p1P/C1N1B1N2/9/3AKAB2 w - - 0 1"
        )
        move = "a2a6"  # Deep-search best defence from the user's JJ game.
        expected = apply_move(board, move)
        geometry = BoardGeometry(
            (1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500)
        )
        app._capture_mouse_board = Mock(
            side_effect=[(board, None, geometry)] * 6
            + [(expected, None, geometry)] * 2
        )
        app._mouse_sleep = Mock()
        app._window_for_geometry = Mock(return_value=42)
        # Depth >= 24 also exercises no_win_reason(); neither a WDL forecast
        # nor a negative engine score is a terminal chess position.
        app.engine.analyse.return_value = (
            [AnalysisLine(1, 30, "cp", -386, [move], (0, 0, 1000))],
            move,
        )
        original_queue_board = app._queue_mouse_board

        def publish(*args):
            original_queue_board(*args)
            if args[1] == expected:
                app.mouse_auto_stop_event.set()

        app._queue_mouse_board = publish
        with (
            patch("app.foreground_window", return_value=42),
            patch("app.user_input_is_idle", return_value=True),
            patch(
                "app.click_screen_move",
                return_value=ClickResult(True, True, 2),
            ) as click,
        ):
            app._mouse_autoplay_worker(
                7, app.mouse_auto_stop_event, "w", "w", 500, 1
            )
        click.assert_called_once()
        statuses = [
            payload[2]
            for kind, payload in app.result_queue.queue
            if kind == "mouse_status"
        ]
        self.assertFalse(any("等待下一局" in status for status in statuses))

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
                self.assertEqual(kwargs["stable_frames"], 1)
                self.assertIsNone(kwargs["max_wait_seconds"])
                self.assertIsNone(kwargs["max_attempts"])
                self.assertTrue(kwargs["tracking_only"])
                self.assertEqual(kwargs["full_relock_every"], 10)
                self.assertIs(kwargs["animation_geometry"], geometry)
                self.assertFalse(kwargs["accept"](initial))  # A sent move is never retried.
                self.assertFalse(kwargs["accept"](initial))
                self.assertTrue(kwargs["accept"](reply))
                kwargs["on_candidate"](reply)  # The explainable frame starts search.
                self.assertTrue(search_started.wait(1))
                self.assertEqual(click.call_count, 1)  # Only the PREVIOUS move clicked.
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
            self.assertEqual(kwargs["pause_seconds"], .18)
            self.assertEqual(kwargs["settle_seconds"], .05)
            from automation import ClickResult
            return ClickResult(True, True, 2)

        with patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True), patch("app.click_screen_move", side_effect=send_move) as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        self.assertEqual(click.call_count, 2)
        self.assertEqual(app.engine.analyse.call_count, 2)  # No second 500ms search after confirming.
        self.assertEqual([payload[1] for kind, payload in app.result_queue.queue if kind == "mouse_done"], ["silent"])


if __name__ == "__main__":
    unittest.main()
