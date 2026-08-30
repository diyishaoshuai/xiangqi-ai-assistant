import threading
import unittest
from unittest.mock import patch

from app import XiangqiApp
from automation import AutomationState


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


if __name__ == "__main__":
    unittest.main()
