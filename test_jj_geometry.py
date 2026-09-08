import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from core import START_FEN, parse_fen
from recognition import NeuralBoardRecognizer


class JJLatticeTests(unittest.TestCase):
    def test_compressed_pose_is_corrected_by_two_full_back_ranks(self):
        board, _ = parse_fen(START_FEN)
        geometry = NeuralBoardRecognizer._geometry_from_keypoints(
            np.float32([[100, 100], [660, 100], [100, 820], [660, 820]]),
            [.2] * 4, rotated=False, image_size=(900, 900),
        )
        circles = np.float32([[[100 + x * 80, 100 + (9 - rank) * 80, 30]
                              for x, rank in board]])
        with patch('recognition.cv2.HoughCircles', return_value=circles):
            corrected = NeuralBoardRecognizer._refine_geometry_from_pieces(
                Image.new('RGB', (900, 900)), geometry, board,
            )
        for square, expected in [((7, 2), (660, 660)), ((4, 2), (420, 660)), ((8, 9), (740, 100))]:
            point = corrected.point_for_square(square)
            self.assertAlmostEqual(point[0], expected[0], delta=1)
            self.assertAlmostEqual(point[1], expected[1], delta=1)

    def test_missing_circles_do_not_invent_a_calibration(self):
        board, _ = parse_fen(START_FEN)
        geometry = NeuralBoardRecognizer._geometry_from_keypoints(
            np.float32([[100, 100], [660, 100], [100, 820], [660, 820]]),
            [.2] * 4, rotated=False, image_size=(900, 900),
        )
        with patch('recognition.cv2.HoughCircles', return_value=None):
            self.assertIs(NeuralBoardRecognizer._refine_geometry_from_pieces(
                Image.new('RGB', (900, 900)), geometry, board), geometry)
