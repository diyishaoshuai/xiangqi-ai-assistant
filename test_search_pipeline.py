import io
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from engine import PikafishEngine
from search_pipeline import ConfirmedSearch
from recognition import NeuralBoardRecognizer, BoardGeometry, TransientBoardFrame
from PIL import Image
import numpy as np


class ConfirmedSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.engine.analyse.return_value = (["candidate"], "a0a1")
        self.search = ConfirmedSearch(self.engine)
        self.addCleanup(self.search.close)
        self.key = self.search.request_key("fen", 500, 1, "history", ["a3a4"])

    def test_starts_before_confirmation_and_reuses_only_exact_key(self):
        self.assertTrue(self.search.offer(self.key))
        self.search.thread.join(1)
        self.assertFalse(self.search.offer(self.key))
        self.assertEqual(self.search.take(self.key, lambda: False), (["candidate"], "a0a1"))
        self.engine.analyse.assert_called_once()
        self.assertEqual(self.engine.analyse.call_args.args, ("fen", 500, 1))
        self.assertEqual(self.engine.analyse.call_args.kwargs["moves"], ("a3a4",))

    def test_history_side_budget_or_candidates_mismatch_discards_result(self):
        for index in range(5):
            with self.subTest(field=index):
                self.search.offer(self.key)
                self.search.thread.join(1)
                changed = list(self.key)
                changed[index] = "different"
                self.assertIsNone(self.search.take(tuple(changed), lambda: False))

    def test_cancellation_after_result_completion_still_prevents_consumption(self):
        self.search.offer(self.key)
        self.search.thread.join(1)
        with self.assertRaises(InterruptedError):
            self.search.take(self.key, lambda: True)

    def test_inflight_replacement_is_cancelled_not_queued(self):
        entered, exited = threading.Event(), threading.Event()

        def analyse(*args, cancelled, **kwargs):
            entered.set()
            exited.wait(1)
            if cancelled():
                raise InterruptedError("stale")
            return [], "b0b1"

        self.engine.analyse.side_effect = analyse
        self.search.offer(self.key)
        self.assertTrue(entered.wait(1))
        new_key = self.search.request_key("new", 500, 1, "history", ["a3a4", "b6b5"])
        self.assertFalse(self.search.offer(new_key))
        self.engine.stop.assert_called_once()
        self.engine.analyse.assert_called_once()
        exited.set()
        self.search.thread.join(1)
        self.assertIsNone(self.search.take(self.key, lambda: False))
        self.assertTrue(self.search.offer(new_key))
        self.assertEqual(self.search.take(new_key, lambda: False), ([], "b0b1"))

    def test_engine_checks_cancel_before_starting_and_go_race_drains_bestmove(self):
        engine = PikafishEngine(Path("unused"))
        engine.start = Mock()
        with self.assertRaises(InterruptedError):
            engine.analyse("fen", 500, 1, cancelled=lambda: True)
        engine.start.assert_not_called()
        engine.process = Mock()
        engine.process.stdout = io.StringIO("info depth 1 multipv 1 score cp 1 pv a0a1\nbestmove a0a1\n")
        commands, stop = [], threading.Event()

        def send(command):
            commands.append(command)
            if command.startswith("go "):
                stop.set()

        engine._send = send
        with self.assertRaises(InterruptedError):
            engine.analyse("fen", 500, 1, cancelled=stop.is_set)
        self.assertEqual(commands[-2:], ["go movetime 500", "stop"])
        self.assertEqual(engine.process.stdout.read(), "")


class TrackingRegionTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = object.__new__(NeuralBoardRecognizer)
        self.points = np.float32([[50, 50], [400, 50], [50, 450], [400, 450]])
        self.geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .9, (800, 500))
        self.recognizer.pose = Mock()
        self.recognizer.pose.pred.return_value = (self.points, np.full(4, .9))
        self.recognizer.classifier = Mock()
        self.recognizer.last_search_bbox = (0, 0, 500, 500)
        self.recognizer.last_image_size = (800, 500)
        self.rows = [["."] * 9 for _ in range(10)]
        self.rows[0][4], self.rows[9][4] = "k", "K"
        unknown = [list(row) for row in self.rows]
        unknown[4][4] = "x"
        self.bad = (None, unknown, np.full((10, 9), .99), "unknown")
        self.good = (None, self.rows, np.full((10, 9), .99), "good")

    def recognize(self):
        return self.recognizer.recognize(Image.new("RGB", (800, 500)),
                                         geometry_hint=self.geometry,
                                         minimum_geometry_confidence=.1, cancelled=lambda: False)

    def test_animation_fails_fast_on_newly_located_known_board(self):
        self.recognizer.classifier.pred.return_value = self.bad
        with self.assertRaises(TransientBoardFrame):
            self.recognize()
        self.assertEqual(self.recognizer.classifier.pred.call_count, 1)
        self.assertEqual(self.recognizer.last_timings["regions"], 1)
        self.assertEqual(self.recognizer.pose.pred.call_args.kwargs["bbox"], [0, 0, 500, 500])

    def test_persistent_failure_still_explores_fallback_regions(self):
        self.recognizer.classifier.pred.side_effect = [self.bad, self.bad, self.bad, self.good]
        for _ in range(2):
            with self.assertRaises(TransientBoardFrame):
                self.recognize()
        _, detections = self.recognize()
        self.assertEqual(len(detections), 2)
        self.assertEqual(self.recognizer.classifier.pred.call_count, 4)
        self.assertEqual(self.recognizer.tracking_failures, 0)

    def test_roi_reuse_still_uses_fresh_pose_and_classification(self):
        self.recognizer.classifier.pred.return_value = self.good
        self.recognize()
        self.assertIsNot(self.recognizer.last_geometry, self.geometry)
        self.recognizer.pose.pred.assert_called_once()
        self.recognizer.classifier.pred.assert_called_once()

    def test_moved_pose_cannot_trigger_same_board_fast_failure(self):
        self.assertFalse(self.recognizer._pose_matches_hint(self.points + 80, self.geometry))
        self.assertTrue(self.recognizer._pose_matches_hint(self.points, self.geometry))
        flipped = BoardGeometry(self.geometry.inverse_matrix, True, .9, (800, 500))
        self.assertTrue(self.recognizer._pose_matches_hint(self.points, flipped))


if __name__ == "__main__":
    unittest.main()
