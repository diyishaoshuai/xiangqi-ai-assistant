"""Colour inference and integration checks. No real mouse/keyboard operations."""
from __future__ import annotations

import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import XiangqiApp
from automation import AutomationState
from core import START_FEN, parse_fen
from player_side import bottom_player_side
from recognition import BoardGeometry


IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class PlayerSideTests(unittest.TestCase):
    def setUp(self):
        self.board, _ = parse_fen(START_FEN)

    def geometry(self, rotated=False, confidence=.9):
        return BoardGeometry(IDENTITY, rotated, confidence, (500, 500))

    def test_normal_and_flipped_images_have_identical_canonical_board_but_different_players(self):
        self.assertEqual(bottom_player_side(self.board, self.geometry()), "w")
        self.assertEqual(bottom_player_side(self.board, self.geometry(True)), "b")

    def test_source_projection_wins_over_orientation_flag_and_piece_population(self):
        reverse = BoardGeometry((-1, 0, 500, 0, -1, 500, 0, 0, 1), False, .9, (600, 600))
        self.assertEqual(bottom_player_side(self.board, reverse), "b")
        endgame = {(3, 2): "K", (5, 7): "k", (8, 9): "R", (7, 9): "C"}
        self.assertEqual(bottom_player_side(endgame, self.geometry()), "w")

    def test_scaled_translated_and_perspective_boards_use_source_coordinates(self):
        for rotated in (False, True):
            geo = BoardGeometry((2, .1, 150, .1, 1.8, 50, .0001, .00015, 1),
                                rotated, .6, (1400, 1100))
            self.assertEqual(bottom_player_side(self.board, geo), "b" if rotated else "w")

    def test_unknown_or_weak_geometry_never_guesses(self):
        for geometry in (None, self.geometry(confidence=.09), self.geometry(confidence=float('nan'))):
            self.assertIsNone(bottom_player_side(self.board, geometry))

    def test_missing_duplicate_or_out_of_palace_kings_never_guess(self):
        for board in ({(4, 0): "K"}, {(4, 0): "K", (3, 0): "K", (4, 9): "k"},
                      {(4, 0): "K", (4, 4): "k"}, {(8, 0): "K", (4, 9): "k"}):
            self.assertIsNone(bottom_player_side(board, self.geometry()))

    def test_king_confidence_is_required_when_available(self):
        for confidence in (.3, .74, float('nan')):
            self.assertIsNone(bottom_player_side(self.board, self.geometry(),
                king_confidences={"K": confidence, "k": .99}))
        self.assertEqual(bottom_player_side(self.board, self.geometry(True),
            king_confidences={"K": .98, "k": .99}), "b")

    def test_sideways_and_out_of_image_boards_are_not_clickable_guesses(self):
        sideways = BoardGeometry((0, 1, 0, -1, 0, 500, 0, 0, 1), False, .9, (600, 600))
        outside = BoardGeometry((1, 0, 0, 0, 1, 600, 0, 0, 1), False, .9, (500, 500))
        self.assertIsNone(bottom_player_side(self.board, sideways))
        self.assertIsNone(bottom_player_side(self.board, outside))

    def make_app(self):
        app = object.__new__(XiangqiApp)
        app.assisted_side, app.side = "w", "w"
        app.auto_player_side_var = Value(True)
        app.player_side_var, app.player_side_hint_var = Value("w"), Value("")
        app.orientation_var, app.status_var = Value(""), Value("")
        app.last_detected_player_side = None
        app.mouse_auto_running = False
        app.logger = Mock()
        app._position_changed, app._record_undo = Mock(), Mock()
        return app

    def test_accepting_detected_black_changes_perspective_but_not_turn_or_board(self):
        app = self.make_app()
        app.board = dict(self.board)
        app._accept_detected_player_side("b")
        self.assertEqual(app.assisted_side, "b")
        self.assertEqual(app.player_side_var.get(), "b")
        self.assertEqual(app.side, "w")
        self.assertEqual(app.board, self.board)
        self.assertIn("自动", app.player_side_hint_var.get())
        app._position_changed.assert_not_called()

    def test_manual_selection_disables_detection_and_can_be_returned_to_auto(self):
        app = self.make_app()
        app.player_side_var.set("b")
        app._player_side_changed()
        self.assertFalse(app.auto_player_side_var.get())
        app._accept_detected_player_side("w")
        self.assertEqual(app.assisted_side, "b")
        app.auto_player_side_var.set(True)
        app._auto_player_side_toggled()
        self.assertEqual(app.assisted_side, "w")
        self.assertEqual(app.side, "w")

    def test_uncertain_observation_retains_current_side_and_invalidates_old_guess(self):
        app = self.make_app()
        app._accept_detected_player_side("b")
        app._accept_detected_player_side(None)
        self.assertEqual(app.assisted_side, "b")
        self.assertIsNone(app.last_detected_player_side)
        self.assertIn("待确认", app.player_side_hint_var.get())

    def test_screenshot_import_infers_player_before_scheduling_analysis(self):
        app = self.make_app()
        app.recognizer = Mock(last_backend="onnx", last_geometry=self.geometry(True))
        detections = [SimpleNamespace(square=sq, piece=piece, confidence=.99) for sq, piece in self.board.items()]
        app.recognizer.recognize.return_value = (None, detections)
        app._position_changed.side_effect = lambda **_kw: self.assertEqual(app.assisted_side, "b")
        app._auto_recognize_image(object())
        self.assertEqual(app.board, self.board)
        self.assertEqual(app.side, "w")

    def test_low_confidence_import_does_not_switch_player(self):
        app = self.make_app()
        app.recognizer = Mock(last_backend="onnx", last_geometry=self.geometry(True))
        detections = [SimpleNamespace(square=sq, piece=piece, confidence=.5 if piece == "K" else .99)
                      for sq, piece in self.board.items()]
        app.recognizer.recognize.return_value = (None, detections)
        app._auto_recognize_image(object())
        self.assertEqual(app.assisted_side, "w")
        self.assertIn("待确认", app.player_side_hint_var.get())

    def test_autoplay_infers_black_without_touching_tk_and_waits_for_red_start(self):
        app = self.make_app()
        app.mouse_auto_detect_side = True
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_session_id, app.closing = 7, False
        app.mouse_auto_running, app.mouse_auto_state = True, AutomationState.ACQUIRING
        app.result_queue, app.engine = queue.Queue(), Mock()
        app._mouse_sleep, app._queue_mouse_status = Mock(), Mock()
        app._window_for_geometry = Mock(return_value=42)
        app._capture_stable_mouse_board = Mock(side_effect=[(self.board, None, self.geometry(True)), InterruptedError("test stop")])
        # Tk accesses from the worker would fail this test immediately.
        app.auto_player_side_var.get = Mock(side_effect=AssertionError("worker accessed Tk"))
        with patch("app.foreground_window", return_value=42), patch("app.click_screen_move") as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "b", 500, 1)
        app.engine.analyse.assert_not_called()
        click.assert_not_called()
        messages = list(app.result_queue.queue)
        self.assertIn(("mouse_player_side", (7, "b")), messages)
        first_board = next(payload for kind, payload in messages if kind == "mouse_board")
        self.assertEqual(first_board[2], "w")
        self.assertEqual(app.assisted_side, "w")  # UI will update through the queue.

    def test_autoplay_refuses_unknown_side_before_engine_or_click(self):
        app = self.make_app()
        app.mouse_auto_detect_side = True
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_session_id, app.closing = 7, False
        app.result_queue, app.engine = queue.Queue(), Mock()
        app._mouse_sleep, app._queue_mouse_status = Mock(), Mock()
        app._window_for_geometry = Mock(return_value=42)
        app._capture_stable_mouse_board = Mock(return_value=(self.board, None, self.geometry(confidence=.01)))
        with patch("app.foreground_window", return_value=42), patch("app.click_screen_move") as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        click.assert_not_called()
        app.engine.analyse.assert_not_called()
        self.assertTrue(any(kind == "mouse_done" and "无法可靠判断" in str(payload)
                            for kind, payload in app.result_queue.queue))

    def test_animation_is_nonblocking_and_never_mutates_position(self):
        from ui_widgets import animate_board
        board = {(0, 1): "R", (4, 0): "K", (4, 9): "k"}
        ui = SimpleNamespace(motion_var=Value(True), _animation_job=None)
        app = SimpleNamespace(board=board, ui=ui, canvas=Mock(), root=Mock(), closing=False,
                              _canvas_point=lambda sq: (sq[0]*50, (9-sq[1])*50))
        before = {(0, 0): "R", (4, 0): "K", (4, 9): "k"}
        points = {(sq, pc): app._canvas_point(sq) for sq, pc in before.items()}
        animate_board(app, before, points)
        app.canvas.move.assert_called_once_with("piece-at-0-1", 0, 50)
        self.assertEqual(app.root.after.call_args.args[0], 16)
        self.assertEqual(app.board, board)

    def test_new_game_can_change_player_from_red_to_black(self):
        app = self.make_app()
        app.mouse_auto_detect_side = True
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_session_id, app.closing = 7, False
        app.result_queue, app.engine = queue.Queue(), Mock()
        app._mouse_sleep, app._queue_mouse_status = Mock(), Mock()
        app._window_for_geometry = Mock(return_value=42)
        app.engine.analyse.return_value = ([], "(none)")  # End first simulated game.
        next_board = dict(self.board)
        next_board[(2, 4)] = next_board.pop((2, 3))
        app._capture_stable_mouse_board = Mock(side_effect=[
            (self.board, None, self.geometry()), (next_board, None, self.geometry(True)),
            InterruptedError("test stop")])
        with patch("app.foreground_window", return_value=42), patch("app.click_screen_move") as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        click.assert_not_called()
        self.assertEqual([payload[1] for kind, payload in app.result_queue.queue
                          if kind == "mouse_player_side"], ["w", "b"])

    def test_orientation_changed_before_click_cancels_the_transaction(self):
        from core import AnalysisLine
        app = self.make_app()
        app.mouse_auto_detect_side = True
        app.mouse_auto_stop_event = threading.Event()
        app.mouse_auto_session_id, app.closing = 7, False
        app.result_queue, app.engine = queue.Queue(), Mock()
        app._mouse_sleep, app._queue_mouse_status = Mock(), Mock()
        app._window_for_geometry = Mock(return_value=42)
        app.engine.analyse.return_value = ([AnalysisLine(1, 20, "cp", 250, ["b2e2"], (600, 400, 0))], "b2e2")
        app._capture_stable_mouse_board = Mock(return_value=(self.board, None, self.geometry()))
        app._wait_for_click_ready = Mock(return_value=("ready", (self.board, None, self.geometry(True))))
        with patch("app.foreground_window", return_value=42), patch("app.click_screen_move") as click:
            app._mouse_autoplay_worker(7, app.mouse_auto_stop_event, "w", "w", 500, 1)
        click.assert_not_called()
        self.assertTrue(any(kind == "mouse_done" and "朝向改变" in str(payload)
                            for kind, payload in app.result_queue.queue))

    def test_hidden_takeover_window_does_not_schedule_move_animation(self):
        from ui_widgets import animate_board
        app = SimpleNamespace(ui=SimpleNamespace(motion_var=Value(True)), root=Mock(), canvas=Mock())
        app.root.winfo_ismapped.return_value = False
        animate_board(app, {}, {})
        app.root.after.assert_not_called()
        app.canvas.move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
