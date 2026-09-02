import unittest
import threading
from unittest.mock import Mock
from pathlib import Path

from PIL import Image

from recognition import BoardGeometry, PieceRecognizer, NeuralBoardRecognizer


CASES = [
    (
        Path(r"C:\Users\ZhuanZ1\AppData\Local\Temp\codex-clipboard-b5e1b8ad-6004-45c0-8e29-de30307003ad.png"),
        {
            **{(x, 9): piece for x, piece in enumerate("rnbakabnr")},
            (1, 7): "c", (7, 7): "c",
            **{(x, 6): "p" for x in range(0, 9, 2)},
            **{(x, 3): "P" for x in range(0, 9, 2)},
            (1, 2): "C", (7, 2): "C",
            **{(x, 0): piece for x, piece in enumerate("RNBAKABNR")},
        },
    ),
    (
        Path(r"C:\Users\ZhuanZ1\AppData\Local\Temp\codex-clipboard-9891a2a1-5b20-4d37-8dca-54c69c6df929.png"),
        {(4, 9): "k", (6, 5): "b", (4, 5): "C", (3, 2): "A", (5, 2): "A", (4, 1): "K", (3, 0): "C"},
    ),
    (
        Path(r"C:\Users\ZhuanZ1\AppData\Local\Temp\codex-clipboard-30ab8001-fcdf-4a46-a759-703c47930c37.png"),
        {(4, 9): "k", (6, 5): "b", (3, 2): "A", (5, 2): "A", (5, 1): "K", (3, 0): "C", (4, 0): "C"},
    ),
    (
        Path(r"C:\Users\ZhuanZ1\AppData\Local\Temp\codex-clipboard-a4a969b7-a686-4251-88b7-7f4c63797b8d.png"),
        {(5, 9): "k", (6, 5): "b", (5, 2): "A", (4, 1): "A", (5, 1): "K", (3, 0): "C", (4, 0): "C"},
    ),
    (
        Path(r"C:\Users\ZhuanZ1\AppData\Local\Temp\codex-clipboard-46957043-909f-40ba-a439-3c577218f098.png"),
        {(5, 9): "k", (3, 7): "a", (2, 4): "B", (4, 4): "C", (4, 3): "C", (4, 0): "K", (6, 0): "B"},
    ),
]


class RecognitionTests(unittest.TestCase):
    def test_progressive_tiles_cover_small_game_windows_anywhere_on_4k_desktop(self):
        boxes = NeuralBoardRecognizer._tiled_candidate_bboxes(2160, 3840)
        self.assertLessEqual(len(boxes), 45)
        for center in ((1600, 1000), (3000, 700), (350, 1700)):
            self.assertTrue(any(left <= center[0] <= right and top <= center[1] <= bottom
                                for left, top, right, bottom in boxes), center)

    def test_standard_start_calibration_recovers_unknown_piece_types_but_not_ambiguous_sides(self):
        import numpy as np
        expected = [list("rnbakabnr"), list("........."), list(".c.....c."),
                    list("p.p.p.p.p"), list("........."), list("........."),
                    list("P.P.P.P.P"), list(".C.....C."), list("........."),
                    list("RNBAKABNR")]
        shifted = [["." for _ in range(9)] for _ in range(10)]
        for row in range(10):
            for column in range(9):
                if expected[row][column] != ".":
                    shifted[row][column] = "p" if row < 5 else "P"
        confidence = np.full((10, 9), .91)
        rows, scores, calibrated = NeuralBoardRecognizer._calibrate_standard_start(
            shifted, confidence)
        self.assertTrue(calibrated)
        self.assertEqual(rows, expected)
        self.assertGreaterEqual(scores[0][4], .78)

        ambiguous = [["x" if expected[row][column] != "." else "." for column in range(9)]
                     for row in range(10)]
        rows, _, calibrated = NeuralBoardRecognizer._calibrate_standard_start(
            ambiguous, confidence)
        self.assertFalse(calibrated)
        self.assertEqual(rows, ambiguous)

    def test_cancellation_never_runs_template_fallback(self):
        recognizer = PieceRecognizer()
        recognizer.neural = Mock()
        recognizer.neural.recognize.side_effect = InterruptedError("stop")
        recognizer.template = Mock()
        with self.assertRaises(InterruptedError):
            recognizer.recognize(Image.new("RGB", (500, 500)), cancelled=lambda: True)
        recognizer.template.recognize.assert_not_called()

    def test_hint_is_clipped_and_not_reused_after_resolution_change(self):
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .9, (500, 500))
        bbox = NeuralBoardRecognizer._hint_bbox(geometry, (500, 500))
        self.assertEqual(len(bbox), 4)
        self.assertTrue(0 <= bbox[0] < 50 < 400 < bbox[2] <= 500)
        self.assertIsNone(NeuralBoardRecognizer._hint_bbox(geometry, (1000, 1000)))

    def test_stop_between_model_stages_skips_classifier(self):
        import numpy as np
        stop = threading.Event()
        recognizer = object.__new__(NeuralBoardRecognizer)
        recognizer.pose = Mock()
        recognizer.classifier = Mock()

        def pose(**_kwargs):
            stop.set()
            return np.float32([[50, 50], [400, 50], [50, 450], [400, 450]]), np.ones(4)

        recognizer.pose.pred.side_effect = pose
        with self.assertRaises(InterruptedError):
            recognizer.recognize(Image.new("RGB", (500, 500)), cancelled=stop.is_set)
        recognizer.classifier.pred.assert_not_called()

    def test_autoplay_skips_low_pose_candidate_instead_of_rejecting_whole_frame(self):
        import numpy as np
        recognizer = object.__new__(NeuralBoardRecognizer)
        keypoints = np.float32([[50, 50], [400, 50], [50, 450], [400, 450]])
        recognizer.pose = Mock()
        recognizer.pose.pred.side_effect = [(keypoints, np.full(4, .09)), (keypoints, np.full(4, .4))]
        rows = [["."] * 9 for _ in range(10)]
        rows[0][4], rows[9][4] = "k", "K"
        recognizer.classifier = Mock()
        recognizer.classifier.pred.return_value = (None, rows, np.full((10, 9), .99), "test")
        recognizer.recognize(Image.new("RGB", (800, 500)), minimum_geometry_confidence=.1)
        self.assertEqual(recognizer.pose.pred.call_count, 2)
        self.assertEqual(recognizer.classifier.pred.call_count, 1)
        self.assertAlmostEqual(recognizer.last_geometry.confidence, .4)

    def test_geometry_maps_both_board_orientations(self):
        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        normal = BoardGeometry(identity, False, 1.0, (500, 500))
        self.assertEqual(normal.point_for_square((0, 0)), (50.0, 450.0))
        self.assertEqual(normal.point_for_square((8, 9)), (400.0, 50.0))
        rotated = BoardGeometry(identity, True, 1.0, (500, 500))
        self.assertEqual(rotated.point_for_square((0, 0)), (400.0, 50.0))
        self.assertEqual(rotated.point_for_square((8, 9)), (50.0, 450.0))

    def test_piece_click_prefers_observed_disc_center(self):
        geometry = BoardGeometry(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            False,
            1.0,
            (500, 500),
            ((4, 0, 227.5, 452.0),),
        )
        self.assertEqual(geometry.point_for_piece((4, 0)), (227.5, 452.0))
        self.assertEqual(
            geometry.point_for_piece((3, 0)),
            geometry.point_for_square((3, 0)),
        )

    def test_fast_geometry_refresh_runs_pose_without_full_classifier(self):
        import numpy as np
        recognizer = object.__new__(NeuralBoardRecognizer)
        keypoints = np.float32([[50, 50], [400, 50], [50, 450], [400, 450]])
        recognizer.pose = Mock()
        recognizer.pose.pred.return_value = keypoints, np.full(4, .8)
        recognizer.classifier = Mock()
        recognizer.last_search_bbox = (0, 0, 500, 500)
        recognizer.last_image_size = (500, 500)
        recognizer.last_geometry = None
        recognizer.last_timings = {}
        hint = BoardGeometry(
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            False,
            .8,
            (500, 500),
        )
        geometry = recognizer.refresh_geometry(
            Image.new("RGB", (500, 500), "white"),
            hint,
            {(4, 0): "K", (4, 9): "k"},
        )
        self.assertAlmostEqual(geometry.confidence, .8)
        recognizer.pose.pred.assert_called_once()
        recognizer.classifier.pred.assert_not_called()
        self.assertIn("fast_pose_ms", recognizer.last_timings)

    def test_supplied_game_screenshots(self):
        available_cases = [case for case in CASES if case[0].exists()]
        if not available_cases:
            self.skipTest("原始临时截图不在当前电脑上")
        recognizer = PieceRecognizer()
        for path, expected in available_cases:
            with self.subTest(path=path.name):
                _, detections = recognizer.recognize(Image.open(path))
                actual = {item.square: item.piece for item in detections}
                self.assertEqual(recognizer.last_backend, "onnx")
                self.assertEqual(actual, expected)

    def test_small_portrait_game_inside_4k_desktop_uses_progressive_search(self):
        path, expected = CASES[0]
        if not path.exists():
            self.skipTest("JJ 象棋截图不在当前电脑上")
        source = Image.open(path).convert("RGB")
        desktop = Image.new("RGB", (3840, 2160), (36, 39, 42))
        game = source.resize((542, 1000))
        desktop.paste(game, (1408, 500))
        recognizer = PieceRecognizer()
        _, detections = recognizer.recognize(
            desktop, minimum_geometry_confidence=.1, cancelled=lambda: False)
        self.assertEqual({item.square: item.piece for item in detections}, expected)
        self.assertGreater(recognizer.neural.last_timings["regions"], 4)
        self.assertGreaterEqual(recognizer.last_geometry.confidence, .1)

    def test_autoplay_tracking_uses_fresh_models_at_multiple_resolutions(self):
        # These original landscape screenshots intentionally exercise a 16:9
        # resize.  The portrait JJ fixture has its own aspect-preserving 4K
        # desktop test above.
        available_cases = [case for case in CASES[1:4] if case[0].exists()]
        if not available_cases:
            self.skipTest("原始临时截图不在当前电脑上")
        recognizer = PieceRecognizer()
        for path, expected in available_cases:
            with Image.open(path) as original:
                for size in (original.size, (1280, 720)):
                    with self.subTest(path=path.name, size=size):
                        image = original.resize(size)
                        recognizer.recognize(image)
                        hint = recognizer.last_geometry
                        for _ in range(2):
                            _, detections = recognizer.recognize(
                                image, geometry_hint=hint, minimum_geometry_confidence=.1,
                                cancelled=lambda: False,
                            )
                            self.assertEqual({item.square: item.piece for item in detections}, expected)
                            self.assertGreaterEqual(recognizer.last_geometry.confidence, .1)
                            self.assertIsNot(recognizer.last_geometry, hint)
                            hint = recognizer.last_geometry


if __name__ == "__main__":
    unittest.main()
