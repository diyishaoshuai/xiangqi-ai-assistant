import unittest
from dataclasses import replace

from recognition import BoardGeometry

from automation import (
    ConfirmationKind,
    CursorUnavailableError,
    HotkeyLatch,
    StableBoardTracker,
    TransitionKind,
    UserInterferenceError,
    classify_board_transition,
    classify_click_confirmation,
    click_screen_move,
    infer_single_move,
    position_is_safe,
    session_event_is_current,
    turn_for_new_game,
    user_input_is_idle,
)
from core import apply_move, parse_fen


class FakeUser32:
    def __init__(self, set_cursor_results):
        self.set_cursor_results = list(set_cursor_results)
        self.position = (0, 0)
        self.mouse_events = []

    def SetCursorPos(self, x, y):
        result = (
            self.set_cursor_results.pop(0)
            if self.set_cursor_results
            else True
        )
        if result:
            self.position = (x, y)
        return result

    def GetCursorPos(self, pointer):
        pointer._obj.x, pointer._obj.y = self.position
        return True

    def mouse_event(self, flag, *_args):
        self.mouse_events.append(flag)


class AutomationTests(unittest.TestCase):
    def test_infers_quiet_move(self):
        board, _ = parse_fen("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        after = apply_move(board, "e1f1")
        self.assertEqual(infer_single_move(board, after, "w"), "e1f1")

    def test_infers_capture(self):
        board, _ = parse_fen("4k4/9/9/9/4p4/4R4/9/9/9/4K4 w - - 0 1")
        after = apply_move(board, "e4e5")
        self.assertEqual(infer_single_move(board, after, "w"), "e4e5")

    def test_rejects_wrong_side_and_multi_move(self):
        board, _ = parse_fen("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        after = apply_move(board, "e1f1")
        self.assertIsNone(infer_single_move(board, after, "b"))
        after[(4, 0)] = after.pop((4, 9))
        self.assertIsNone(infer_single_move(board, after, "w"))

    def test_requires_both_kings(self):
        board, _ = parse_fen("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        self.assertTrue(position_is_safe(board))
        board.pop((4, 9))
        self.assertFalse(position_is_safe(board))

    def test_classifies_same_move_terminal_and_ambiguous(self):
        board, _ = parse_fen("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        self.assertEqual(
            classify_board_transition(board, board, "w").kind,
            TransitionKind.SAME,
        )
        moved = apply_move(board, "e1f1")
        transition = classify_board_transition(board, moved, "w")
        self.assertEqual(transition.kind, TransitionKind.MOVE)
        self.assertEqual(transition.move, "e1f1")
        captured = apply_move(board, "e1e9")
        terminal = classify_board_transition(board, captured, "w")
        self.assertEqual(terminal.kind, TransitionKind.TERMINAL_MOVE)
        changed_twice = dict(moved)
        changed_twice[(4, 0)] = changed_twice.pop((4, 9))
        self.assertEqual(
            classify_board_transition(board, changed_twice, "w").kind,
            TransitionKind.AMBIGUOUS,
        )

    def test_click_confirmation_accepts_expected_and_fast_reply(self):
        before, _ = parse_fen(
            "4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1"
        )
        expected = apply_move(before, "e1f1")
        self.assertEqual(
            classify_click_confirmation(before, expected, before, "b").kind,
            ConfirmationKind.UNCHANGED,
        )
        self.assertEqual(
            classify_click_confirmation(before, expected, expected, "b").kind,
            ConfirmationKind.EXPECTED,
        )
        fast_reply = apply_move(expected, "e9f9")
        confirmed = classify_click_confirmation(
            before,
            expected,
            fast_reply,
            "b",
        )
        self.assertEqual(confirmed.kind, ConfirmationKind.FAST_REPLY)
        self.assertEqual(confirmed.move, "e9f9")
        ambiguous = dict(fast_reply)
        ambiguous[(3, 0)] = ambiguous.pop((4, 0))
        self.assertEqual(
            classify_click_confirmation(
                before,
                expected,
                ambiguous,
                "b",
            ).kind,
            ConfirmationKind.AMBIGUOUS,
        )

    def test_standard_new_game_is_red_to_move(self):
        standard, _ = parse_fen(
            "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/"
            "P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
        )
        custom = {(4, 9): "k", (4, 0): "K"}
        self.assertEqual(turn_for_new_game(standard, standard, "b"), "w")
        self.assertEqual(turn_for_new_game(custom, standard, "b"), "b")

    def test_hotkey_latch_uses_edges_and_debounce(self):
        latch = HotkeyLatch(debounce_seconds=0.25)
        self.assertTrue(latch.update(True, now=0.0))
        self.assertFalse(latch.update(True, now=0.10))
        self.assertFalse(latch.update(False, now=0.12))
        self.assertFalse(latch.update(True, now=0.20))
        self.assertFalse(latch.update(False, now=0.22))
        self.assertTrue(latch.update(True, now=0.30))

    def test_stable_board_tracker_resets_on_interference(self):
        board, _ = parse_fen("4k4/9/9/9/9/9/9/9/4R4/4K4 w - - 0 1")
        tracker = StableBoardTracker(3)
        self.assertFalse(tracker.observe(board))
        self.assertFalse(tracker.observe(board))
        self.assertFalse(tracker.observe(board, accepted=False))
        self.assertFalse(tracker.observe(board))
        self.assertFalse(tracker.observe(board))
        self.assertTrue(tracker.observe(board))

    def test_user_idle_threshold_is_testable(self):
        self.assertFalse(user_input_is_idle(1.2, elapsed_seconds=1.19))
        self.assertTrue(user_input_is_idle(1.2, elapsed_seconds=1.20))

    def test_old_session_events_are_rejected(self):
        self.assertFalse(
            session_event_is_current(4, 5, running=True)
        )
        self.assertFalse(
            session_event_is_current(5, 5, running=True, stopping=True)
        )
        self.assertTrue(
            session_event_is_current(5, 5, running=True)
        )

    def test_cursor_move_recovers_and_returns_verified_result(self):
        user32 = FakeUser32([False, True, True])
        result = click_screen_move(
            (10, 20),
            (30, 40),
            lambda: False,
            pause_seconds=0,
            user32=user32,
            sleep=lambda _seconds: None,
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.cursor_attempts, 3)
        self.assertEqual(len(user32.mouse_events), 4)

    def test_cursor_failure_never_sends_mouse_down(self):
        user32 = FakeUser32([False, False, False])
        with self.assertRaises(CursorUnavailableError):
            click_screen_move(
                (10, 20),
                (30, 40),
                lambda: False,
                pause_seconds=0,
                user32=user32,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(user32.mouse_events, [])

    def test_stop_between_clicks_prevents_second_click(self):
        user32 = FakeUser32([True])

        with self.assertRaises(InterruptedError):
            click_screen_move(
                (10, 20),
                (30, 40),
                lambda: len(user32.mouse_events) >= 2,
                pause_seconds=0.2,
                user32=user32,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(len(user32.mouse_events), 2)

    def test_stop_after_cursor_position_before_press_sends_no_click(self):
        user32 = FakeUser32([True])
        stopped = False

        def pause(_seconds):
            nonlocal stopped
            stopped = True

        with self.assertRaises(InterruptedError):
            click_screen_move((10, 20), (30, 40), lambda: stopped,
                              user32=user32, sleep=pause)
        self.assertEqual(user32.mouse_events, [])

    def test_stability_also_requires_non_jumping_click_coordinates(self):
        board = {(4, 0): "K", (4, 9): "k"}
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .9, (1000, 800))
        moved = replace(geometry, inverse_matrix=(1, 0, 100, 0, 1, 0, 0, 0, 1))
        tracker = StableBoardTracker(2)
        self.assertFalse(tracker.observe(board, geometry=geometry))
        self.assertFalse(tracker.observe(board, geometry=moved))
        self.assertTrue(tracker.observe(board, geometry=moved))

    def test_window_change_cancels_transaction_after_first_click(self):
        user32 = FakeUser32([True])

        with self.assertRaises(UserInterferenceError) as raised:
            click_screen_move(
                (10, 20),
                (30, 40),
                lambda: False,
                pause_seconds=0.2,
                guard=lambda: not user32.mouse_events,
                user32=user32,
                sleep=lambda _seconds: None,
            )
        self.assertTrue(raised.exception.first_click_sent)
        self.assertEqual(len(user32.mouse_events), 2)


if __name__ == "__main__":
    unittest.main()
