import unittest

from automation import infer_single_move, position_is_safe
from core import apply_move, parse_fen


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


if __name__ == "__main__":
    unittest.main()
