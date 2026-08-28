import unittest

from app import prefer_quiet_winning_line
from core import AnalysisLine, parse_fen


class AppSelectionTests(unittest.TestCase):
    def setUp(self):
        self.board, self.side = parse_fen(
            "4k4/9/9/9/9/9/9/9/R8/3K5 w - - 0 1"
        )

    def test_repeated_checks_yield_to_quiet_winning_move(self):
        lines = [
            AnalysisLine(1, 32, "mate", 10, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 650, ["a1a2"], (1000, 0, 0)),
        ]
        promoted, move = prefer_quiet_winning_line(lines, self.board, self.side)
        self.assertEqual(move, "a1a2")
        self.assertEqual(promoted[0].best_move, "a1a2")

    def test_short_forced_mate_keeps_checking(self):
        lines = [
            AnalysisLine(1, 32, "mate", 3, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 650, ["a1a2"], (1000, 0, 0)),
        ]
        promoted, move = prefer_quiet_winning_line(lines, self.board, self.side)
        self.assertIsNone(move)
        self.assertEqual(promoted[0].best_move, "a1a9")

    def test_quiet_move_must_keep_winning_chances(self):
        lines = [
            AnalysisLine(1, 32, "cp", 800, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 0, ["a1a2"], (0, 1000, 0)),
        ]
        promoted, move = prefer_quiet_winning_line(lines, self.board, self.side)
        self.assertIsNone(move)
        self.assertEqual(promoted[0].best_move, "a1a9")

    def test_nearly_drawn_quiet_move_is_not_promoted(self):
        lines = [
            AnalysisLine(1, 32, "mate", 10, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 38, ["a1a2"], (2, 998, 0)),
        ]
        promoted, move = prefer_quiet_winning_line(lines, self.board, self.side)
        self.assertIsNone(move)
        self.assertEqual(promoted[0].best_move, "a1a9")


if __name__ == "__main__":
    unittest.main()
