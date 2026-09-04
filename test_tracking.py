import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from core import START_FEN, apply_move, parse_fen
from recognition import BoardGeometry
from tracking import (
    BoardObservation,
    DiagnosticRecorder,
    FrameSource,
    JJ_PROFILE,
    LegalStateEstimator,
    MoveTransaction,
    SquareEvidence,
    TransactionState,
    add_frame_motion,
    evidence_from_model_scores,
    select_platform_profile,
    track_geometry_with_optical_flow,
)


def exact_observation(board, *, geometry=None):
    squares = {}
    for rank in range(10):
        for file_index in range(9):
            square = (file_index, rank)
            piece = board.get(square)
            pieces = {name: 0.0001 for name in "KABNRCPkabnrcp"}
            if piece is None:
                empty, red, black = 0.995, 0.0025, 0.0025
            else:
                pieces[piece] = 0.995
                empty = 0.001
                red, black = ((0.998, 0.001) if piece.isupper() else (0.001, 0.998))
            squares[square] = SquareEvidence(empty, red, black, pieces)
    return BoardObservation(squares, geometry or object(), dict(board), True, "test")


class EvidenceTests(unittest.TestCase):
    def test_model_distribution_keeps_occupancy_side_and_piece_probabilities(self):
        scores = [[0.0] * 16 for _ in range(90)]
        scores[0][0] = 0.10
        scores[0][1] = 0.05
        scores[0][6] = 0.55  # red rook
        scores[0][13] = 0.30  # black rook
        evidence = evidence_from_model_scores(scores, rotated=False)[(0, 9)]
        self.assertAlmostEqual(evidence.empty, 0.10)
        self.assertAlmostEqual(evidence.occluded, 0.05)
        self.assertAlmostEqual(evidence.red, 0.55)
        self.assertAlmostEqual(evidence.black, 0.30)
        self.assertAlmostEqual(evidence.pieces["R"], 0.55)

    def test_model_coordinates_rotate_with_the_board(self):
        scores = [[0.0] * 16 for _ in range(90)]
        scores[0][2] = 1.0
        self.assertGreater(evidence_from_model_scores(scores, rotated=True)[(8, 0)].pieces["K"], 0.99)

    def test_motion_is_measured_per_square_not_from_surrounding_ui(self):
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500))
        board = {(4, 0): "K", (4, 9): "k"}
        observation = exact_observation(board, geometry=geometry)
        previous = Image.new("RGB", (500, 500), "white")
        current = previous.copy()
        current.paste("black", (45, 440, 56, 451))
        measured, stable = add_frame_motion(observation.squares, previous, current, geometry)
        self.assertGreater(measured[(0, 0)].motion, .1)
        self.assertEqual(measured[(8, 9)].motion, 0)
        self.assertTrue(stable)


class StateEstimatorTests(unittest.TestCase):
    def test_expected_position_requires_two_stable_frames(self):
        before, _ = parse_fen(START_FEN)
        move = "c3c4"
        expected = apply_move(before, move)
        estimator = LegalStateEstimator(required_frames=2)
        first = estimator.observe(exact_observation(expected), before, "w", pending_move=move, expected=expected)
        second = estimator.observe(exact_observation(expected), before, "w", pending_move=move, expected=expected)
        self.assertEqual(first.kind, "expected")
        self.assertFalse(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.board, expected)

    def test_fast_opponent_reply_can_be_committed_as_one_legal_path(self):
        before, _ = parse_fen(START_FEN)
        move, reply = "c3c4", "h7c7"
        expected = apply_move(before, move)
        replied = apply_move(expected, reply)
        estimator = LegalStateEstimator(required_frames=2)
        estimator.observe(exact_observation(replied), before, "w", pending_move=move, expected=expected)
        estimate = estimator.observe(exact_observation(replied), before, "w", pending_move=move, expected=expected)
        self.assertEqual(estimate.kind, "fast_reply")
        self.assertTrue(estimate.accepted)
        self.assertEqual(estimate.moves, (move, reply))

    def test_one_occluded_unrelated_square_does_not_veto_expected_move(self):
        before, _ = parse_fen(START_FEN)
        move = "c3c4"
        expected = apply_move(before, move)
        observation = exact_observation(expected)
        squares = dict(observation.squares)
        squares[(8, 9)] = SquareEvidence(.02, .02, .02, {}, occluded=.99, motion=.9)
        noisy = BoardObservation(squares, observation.geometry, expected, False, "jj")
        estimator = LegalStateEstimator(required_frames=1)
        estimate = estimator.observe(noisy, before, "w", pending_move=move, expected=expected)
        self.assertTrue(estimate.accepted)
        self.assertEqual(estimate.kind, "expected")


class TransactionTests(unittest.TestCase):
    def test_destination_can_never_be_sent_twice(self):
        transaction = MoveTransaction("a0a1", {}, {})
        transaction.source_sent()
        transaction.destination_sent()
        for _ in range(20):
            with self.assertRaises(RuntimeError):
                transaction.destination_sent()
        self.assertEqual(transaction.destination_send_count, 1)

    def test_uncertain_transaction_remains_non_retryable_and_can_recover(self):
        transaction = MoveTransaction("a0a1", {}, {})
        transaction.destination_sent()
        transaction.observe()
        transaction.uncertain()
        self.assertEqual(transaction.state, TransactionState.UNCERTAIN)
        with self.assertRaises(RuntimeError):
            transaction.destination_sent()
        transaction.commit(terminal=True)
        self.assertEqual(transaction.state, TransactionState.TERMINAL)


class PlatformAndCaptureTests(unittest.TestCase):
    def test_jj_profile_is_selected_from_title_or_process(self):
        self.assertIs(select_platform_profile("JJ象棋", "game.exe"), JJ_PROFILE)
        self.assertIs(select_platform_profile("象棋", "JJGame.exe"), JJ_PROFILE)

    def test_frame_source_falls_back_to_pillow(self):
        class BrokenDxcam:
            @staticmethod
            def create(**_kwargs):
                raise RuntimeError("desktop duplication unavailable")

        source = FrameSource(dxcam_module=BrokenDxcam)
        image = Image.new("RGB", (32, 24), "green")
        with patch("tracking.ImageGrab.grab", return_value=image):
            captured = source.grab()
        self.assertEqual(captured.size, (32, 24))
        self.assertEqual(source.backend, "pillow")
        self.assertEqual(len(source.frames), 1)

    def test_optical_flow_updates_a_small_locked_board_translation(self):
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (500, 500))
        previous = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(previous)
        for x in range(50, 401, 44):
            draw.line((x, 50, x, 450), fill="black", width=2)
        for y in range(50, 451, 44):
            draw.line((50, y, 400, y), fill="black", width=2)
        current = Image.new("RGB", previous.size, "white")
        current.paste(previous, (4, 3))
        tracked, displacement, reliable = track_geometry_with_optical_flow(previous, current, geometry)
        self.assertTrue(reliable)
        self.assertAlmostEqual(displacement, 5.0, delta=1.0)
        old_point = geometry.point_for_square((4, 4))
        new_point = tracked.point_for_square((4, 4))
        self.assertAlmostEqual(new_point[0] - old_point[0], 4.0, delta=1.0)
        self.assertAlmostEqual(new_point[1] - old_point[1], 3.0, delta=1.0)

    def test_diagnostics_are_local_bounded_and_clearable(self):
        geometry = BoardGeometry((1, 0, 0, 0, 1, 0, 0, 0, 1), False, .8, (100, 100))
        frames = [(time.monotonic(), Image.new("RGB", (100, 100), "white")) for _ in range(15)]
        with tempfile.TemporaryDirectory() as temporary:
            recorder = DiagnosticRecorder(Path(temporary), max_events=2, max_bytes=10_000_000)
            for number in range(3):
                target = recorder.save("uncertain", frames, {"number": number}, geometry)
                payload = json.loads((target / "state.json").read_text(encoding="utf-8"))
                self.assertLessEqual(payload["frame_count"], 12)
                time.sleep(.01)
            self.assertEqual(len([path for path in Path(temporary).iterdir() if path.is_dir()]), 2)
            recorder.clear()
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
