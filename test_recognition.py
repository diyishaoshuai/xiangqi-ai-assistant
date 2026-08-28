import unittest
from pathlib import Path

from PIL import Image

from recognition import BoardGeometry, PieceRecognizer


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
    def test_geometry_maps_both_board_orientations(self):
        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        normal = BoardGeometry(identity, False, 1.0, (500, 500))
        self.assertEqual(normal.point_for_square((0, 0)), (50.0, 450.0))
        self.assertEqual(normal.point_for_square((8, 9)), (400.0, 50.0))
        rotated = BoardGeometry(identity, True, 1.0, (500, 500))
        self.assertEqual(rotated.point_for_square((0, 0)), (400.0, 50.0))
        self.assertEqual(rotated.point_for_square((8, 9)), (50.0, 450.0))

    def test_supplied_game_screenshots(self):
        recognizer = PieceRecognizer()
        for path, expected in CASES:
            with self.subTest(path=path.name):
                _, detections = recognizer.recognize(Image.open(path))
                actual = {item.square: item.piece for item in detections}
                self.assertEqual(recognizer.last_backend, "onnx")
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
