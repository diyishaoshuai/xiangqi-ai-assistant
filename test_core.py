import unittest

from core import (
    PUZZLE_FEN,
    apply_move,
    describe_move,
    find_direct_king_capture,
    make_fen,
    move_gives_check,
    move_notation,
    no_win_reason,
    parse_fen,
    score_text,
    AnalysisLine,
)


class CoreTests(unittest.TestCase):
    def test_fen_roundtrip(self):
        board, side = parse_fen(PUZZLE_FEN)
        self.assertEqual(make_fen(board, side), PUZZLE_FEN)

    def test_known_solution_notation(self):
        board, _ = parse_fen(PUZZLE_FEN)
        self.assertEqual(move_notation(board, "e1f1"), "帅五平四")
        board = apply_move(board, "e1f1")
        board = apply_move(board, "e9e8")
        self.assertEqual(move_notation(board, "e5e0"), "炮五退五")

    def test_black_notation(self):
        board, _ = parse_fen(PUZZLE_FEN)
        board = apply_move(board, "e1f1")
        self.assertEqual(move_notation(board, "e9f9"), "将5平6")
        self.assertEqual(move_notation(board, "g5i7"), "象7退9")

    def test_score_label(self):
        line = AnalysisLine(1, 20, "mate", 8, ["e1f1"])
        self.assertEqual(score_text(line, "w"), "红方杀 8")

    def test_zero_score_is_draw(self):
        line = AnalysisLine(1, 245, "cp", 0, ["f4f2"], (0, 1000, 0))
        self.assertEqual(score_text(line, "w"), "和棋 0.00")
        self.assertIn("理论和棋", no_win_reason(line))

    def test_no_win_requires_stable_depth(self):
        line = AnalysisLine(1, 12, "cp", 0, ["f4f2"], (0, 1000, 0))
        self.assertIsNone(no_win_reason(line))

    def test_forced_loss_needs_no_depth_threshold(self):
        line = AnalysisLine(1, 8, "mate", -5, ["e1e0"])
        self.assertIn("被将死", no_win_reason(line))

    def test_direct_rook_capture_of_king(self):
        fen = "9/4Rk3/9/9/9/P8/9/9/4A4/3AK4 w - - 0 1"
        board, side = parse_fen(fen)
        self.assertEqual(find_direct_king_capture(board, side), "e8f8")
        self.assertIn("吃将", describe_move(board, "e8f8"))

    def test_blocked_rook_cannot_capture_king(self):
        board, side = parse_fen("9/4RAk2/9/9/9/9/9/9/9/4K4 w - - 0 1")
        self.assertIsNone(find_direct_king_capture(board, side))

    def test_move_gives_check(self):
        board, side = parse_fen("4k4/9/9/9/9/9/9/9/R8/3K5 w - - 0 1")
        self.assertTrue(move_gives_check(board, side, "a1a9"))
        self.assertFalse(move_gives_check(board, side, "a1a2"))


if __name__ == "__main__":
    unittest.main()
