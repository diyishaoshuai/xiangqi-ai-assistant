import unittest
import threading
from unittest.mock import Mock
from pathlib import Path

from PIL import Image

from recognition import BoardGeometry, PieceRecognizer, NeuralBoardRecognizer


CASES = [
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

    def test_autoplay_tracking_uses_fresh_models_at_multiple_resolutions(self):
        available_cases = [case for case in CASES[:3] if case[0].exists()]
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
