import queue
import threading
import unittest
from unittest.mock import Mock, patch
from app import XiangqiApp
from core import START_FEN, AnalysisLine, make_fen, parse_fen, no_win_reason
from engine import EngineSearchResult


def result(move, depth=25, trusted=True, elapsed=0):
    return EngineSearchResult([AnalysisLine(1, depth, 'cp', 30, [move], (30,950,20))],
                              move, None, depth, 4, trusted, 'complete_iteration', elapsed)


class UnifiedSafetyTests(unittest.TestCase):
    def app(self):
        app = object.__new__(XiangqiApp)
        app.board, app.side = parse_fen(START_FEN)
        app.analysis_board, app.analysis_side = dict(app.board), app.side
        app.assisted_side = app.side
        app.closing = False
        app.analysis_generation = 7
        app.engine, app.logger = Mock(), Mock()
        app.result_queue = queue.Queue()
        app.status_var = Mock()
        app.follow_blocked_positions = {}
        return app

    def test_zero_win_does_not_block_follow_or_show_modal(self):
        app = self.app()
        app.follow_move_pending = True
        app.follow_move_description = 'still pending'
        line = AnalysisLine(1,27,'cp',-85,['c3c4'],(0,657,343))
        with patch('app.messagebox.showwarning') as warning:
            self.assertTrue(app._handle_no_win(line, '-0.85'))
        warning.assert_not_called()
        self.assertTrue(app.follow_move_pending)
        self.assertEqual(app.follow_move_description, 'still pending')
        self.assertEqual(app.follow_blocked_positions, {})
        self.assertNotIn('建议认输', app.status_var.set.call_args.args[0])
        self.assertIn('不等于', no_win_reason(line))

    def test_opponent_cannot_move_twice_or_reset_history(self):
        app = self.app()
        app.selected_square = (0,6)  # Black pawn while red must move.
        app.follow_move_pending = False
        app.follow_move_description = None
        app._record_undo = Mock()
        app._position_changed = Mock()
        before = dict(app.board)
        app._move_selected_to((0,5))
        self.assertEqual(before, app.board)
        app._record_undo.assert_not_called()
        app._position_changed.assert_not_called()

    def test_manual_follow_verifies_mismatched_bestmove(self):
        app = self.app()
        bad = result('c3c4', trusted=False)
        bad.bestmove = 'a3a4'
        bad.trust_reason = 'bestmove_pv_mismatch'
        app.engine.analyse.side_effect = [bad, result('b0c2', depth=28)]
        app._analysis_worker(START_FEN, 500, 1, 7, app.board, app.side, False, START_FEN, [], set(), False)
        messages = list(app.result_queue.queue)
        final = next(payload for kind,payload in messages if kind == 'analysis')
        self.assertEqual(final[2], 'b0c2')
        self.assertEqual(final[1][0].best_move, 'b0c2')
        self.assertEqual(app.engine.analyse.call_count, 2)

    def test_position_change_during_review_publishes_no_move(self):
        app = self.app()
        def search(*args, **kwargs):
            app.analysis_generation += 1
            return result('c3c4')
        app.engine.analyse.side_effect = search
        app._analysis_worker(START_FEN,500,1,7,app.board,app.side,False,START_FEN,[],set(),False)
        self.assertFalse(any(kind == 'analysis' for kind,_ in app.result_queue.queue))

    def test_recorded_i9i8_c9e7_disagreement_is_reviewed(self):
        app = self.app()
        fen = 'r1bakab1r/9/1cn3n2/p1p1p1p1p/c8/2P6/P3P1P1P/NC2C1N2/9/R1BAKABR1 b - - 0 1'
        app.board,app.side = parse_fen(fen)
        app.assisted_side = app.side
        bad = result('i9i8',depth=23,trusted=False,elapsed=3000)
        bad.bestmove = 'c9e7'
        bad.trust_reason = 'bestmove_pv_mismatch'
        app.engine.analyse.side_effect = [bad,result('c9e7',depth=26)]
        app._analysis_worker(fen,3000,1,7,app.board,app.side,False,fen,[],set(),False)
        final = next(payload for kind,payload in app.result_queue.queue if kind=='analysis')
        self.assertEqual(final[2],'c9e7')
        self.assertEqual(app.engine.analyse.call_count,2)

    def test_review_uses_remaining_total_budget(self):
        app = self.app()
        fast = result('c3c4', depth=10, elapsed=500)
        app.engine.analyse.return_value = result('b0c2')
        with patch('app.time.monotonic', return_value=100):
            chosen = app._choose_autoplay_result(7,threading.Event(),app.board,app.side,
                START_FEN,START_FEN,[],500,fast,set(),False,
                cancelled_callback=lambda:False,status_callback=lambda _:None)
        self.assertEqual(app.engine.analyse.call_args.args[1],2500)
        self.assertEqual(chosen.bestmove,'b0c2')


if __name__ == '__main__': unittest.main()
