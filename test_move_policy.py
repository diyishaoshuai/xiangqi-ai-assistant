import threading
import unittest
from unittest.mock import Mock

from app import XiangqiApp
from core import AnalysisLine, START_FEN, parse_fen
from engine import EngineSearchResult
from move_policy import MoveDecisionPolicy


def result(move, *, depth=20, score=0, wdl=(0, 1000, 0), stable=3, trusted=True):
    return EngineSearchResult(
        [AnalysisLine(1, depth, "cp", score, [move], wdl)],
        move,
        None,
        depth,
        stable,
        trusted,
        "test",
        0.0,
    )


class MoveRiskTests(unittest.TestCase):
    def test_safe_stable_move_keeps_base_budget(self):
        board, side = parse_fen(START_FEN)
        report = MoveDecisionPolicy().assess(board, side, result("c3c4", score=40))
        self.assertEqual(report.severity, "safe")
        self.assertEqual(report.verification_ms, 0)

    def test_low_depth_or_unstable_root_uses_three_seconds(self):
        board, side = parse_fen(START_FEN)
        report = MoveDecisionPolicy().assess(
            board, side, result("c3c4", depth=12, stable=1, score=40)
        )
        self.assertEqual(report.severity, "medium")
        self.assertEqual(report.verification_ms, 3000)

    def test_rook_for_cannon_is_high_risk(self):
        board, side = parse_fen(
            "2bakcb2/2cR5/n8/C5p2/8p/2p3P2/P3r3P/4B4/4A4/3A1KB2 w - - 0 1"
        )
        report = MoveDecisionPolicy().assess(
            board, side, result("d8c8", depth=12, score=-469, wdl=(0, 0, 1000))
        )
        self.assertEqual(report.severity, "high")
        self.assertEqual(report.verification_ms, 5000)

    def test_ignoring_attacked_cannon_is_high_risk(self):
        board, side = parse_fen(
            "2bakcb2/2n6/9/5Cp2/8p/2p3P2/5r2P/4B4/4A4/3AK1B2 w - - 0 1"
        )
        report = MoveDecisionPolicy().assess(
            board, side, result("g4g5", depth=13, score=-525, wdl=(0, 0, 1000))
        )
        self.assertEqual(report.severity, "high")
        self.assertTrue(any("大子" in reason or "立即吃掉" in reason for reason in report.reasons))


class AdaptiveDecisionTests(unittest.TestCase):
    def setUp(self):
        # Budget assertions test policy tiers, not host scheduler jitter.
        from unittest.mock import patch
        clock = patch('app.time.monotonic', return_value=100.0)
        clock.start()
        self.addCleanup(clock.stop)

    def make_app(self):
        app = object.__new__(XiangqiApp)
        app.engine = Mock()
        app.logger = Mock()
        app.move_decision_policy = MoveDecisionPolicy()
        app.closing = False
        app.mouse_auto_session_id = 7
        app._queue_mouse_status = Mock()
        return app

    def test_rook_blunder_is_replaced_by_five_second_result(self):
        app = self.make_app()
        board, side = parse_fen(
            "2bakcb2/2cR5/n8/C5p2/8p/2p3P2/P3r3P/4B4/4A4/3A1KB2 w - - 0 1"
        )
        fast = result("d8c8", depth=12, score=-469, wdl=(0, 0, 1000), stable=1)
        deep = result("d8g8", depth=18, score=-491, wdl=(0, 0, 1000))
        app.engine.analyse.return_value = deep
        chosen = app._choose_autoplay_result(
            7, threading.Event(), board, side, "fen", "history", [], 500, fast, set(), False
        )
        self.assertEqual(chosen.bestmove, "d8g8")
        self.assertEqual(app.engine.analyse.call_args.args[1:3], (5000, 1))

    def test_cannon_blunder_is_replaced_by_five_second_result(self):
        app = self.make_app()
        board, side = parse_fen(
            "2bakcb2/2n6/9/5Cp2/8p/2p3P2/5r2P/4B4/4A4/3AK1B2 w - - 0 1"
        )
        fast = result("g4g5", depth=13, score=-525, wdl=(0, 0, 1000), stable=1)
        deep = result("f6e6", depth=18, score=-575, wdl=(0, 0, 1000))
        app.engine.analyse.return_value = deep
        chosen = app._choose_autoplay_result(
            7, threading.Event(), board, side, "fen", "history", [], 500, fast, set(), False
        )
        self.assertEqual(chosen.bestmove, "f6e6")

    def test_verified_sacrifice_is_allowed(self):
        app = self.make_app()
        board, side = parse_fen(
            "2bakcb2/2cR5/n8/C5p2/8p/2p3P2/P3r3P/4B4/4A4/3A1KB2 w - - 0 1"
        )
        sacrifice = result("d8c8", depth=18, score=-469, wdl=(0, 0, 1000))
        app.engine.analyse.return_value = sacrifice
        chosen = app._choose_autoplay_result(
            7, threading.Event(), board, side, "fen", "history", [], 500, sacrifice, set(), False
        )
        self.assertEqual(chosen.bestmove, "d8c8")

    def test_repetition_uses_searchmoves_without_multipv_override(self):
        app = self.make_app()
        board, side = parse_fen(START_FEN)
        fast = result("c3c4", depth=20, score=40)
        alternative = result("b0c2", depth=20, score=20)
        app.engine.analyse.return_value = alternative
        chosen = app._choose_autoplay_result(
            7,
            threading.Event(),
            board,
            side,
            START_FEN,
            START_FEN,
            [],
            500,
            fast,
            {"c3c4"},
            False,
        )
        self.assertEqual(chosen.bestmove, "b0c2")
        self.assertEqual(app.engine.analyse.call_args.args[1:3], (3000, 1))
        root_moves = app.engine.analyse.call_args.kwargs["root_moves"]
        self.assertNotIn("c3c4", root_moves)
        self.assertIn("b0c2", root_moves)

    def test_f1_during_verification_never_returns_a_move(self):
        app = self.make_app()
        board, side = parse_fen(START_FEN)
        fast = result("c3c4", depth=10, score=40, stable=1)
        stop = threading.Event()

        def cancel(*args, **kwargs):
            stop.set()
            raise InterruptedError("cancelled")

        app.engine.analyse.side_effect = cancel
        with self.assertRaises(InterruptedError):
            app._choose_autoplay_result(
                7, stop, board, side, START_FEN, START_FEN, [], 500, fast, set(), False
            )

    def test_verification_mismatch_executes_last_complete_legal_pv(self):
        app = self.make_app()
        board, side = parse_fen(START_FEN)
        fast = result("c3c4", depth=10, score=40, stable=1)
        mismatch = EngineSearchResult(
            [AnalysisLine(1, 18, "cp", 35, ["b0c2"], (300, 700, 0))],
            "c3c4",
            None,
            18,
            0,
            False,
            "bestmove_pv_mismatch",
            3000.0,
        )
        app.engine.analyse.return_value = mismatch
        chosen = app._choose_autoplay_result(
            7, threading.Event(), board, side, START_FEN, START_FEN, [], 500, fast, set(), False
        )
        self.assertEqual(chosen.bestmove, "b0c2")
        self.assertEqual(chosen.trust_reason, "fallback_last_complete_pv")


if __name__ == "__main__":
    unittest.main()
