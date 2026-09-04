import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoplay_state import load_autoplay_state, save_autoplay_state
from core import START_FEN, apply_move, parse_fen


class AutoplayStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "autoplay-state.json"
        self.path_patch = patch("autoplay_state.state_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_round_trip_including_pending_destination(self):
        board, side = parse_fen(START_FEN)
        board = apply_move(board, "c3c4")
        side = "b"
        pending = apply_move(board, "h7c7")
        save_autoplay_state(
            board,
            side,
            START_FEN,
            ["c3c4"],
            "jj",
            pending_board=pending,
            pending_side="w",
            pending_history_fen=START_FEN,
            pending_moves=["c3c4", "h7c7"],
        )
        restored = load_autoplay_state()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.platform, "jj")
        self.assertEqual(restored.moves, ("c3c4",))
        self.assertEqual(restored.pending.moves, ("c3c4", "h7c7"))
        self.assertEqual(restored.resume_tuple()[0], board)

    def test_corrupt_or_illegal_history_is_deleted(self):
        payload = {
            "schema_version": 1,
            "saved_at": time.time(),
            "board_fen": START_FEN,
            "side": "w",
            "history_fen": START_FEN,
            "moves": ["a0a9"],
            "platform": "jj",
            "pending": None,
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(load_autoplay_state())
        self.assertFalse(self.path.exists())

    def test_stale_state_is_deleted(self):
        board, side = parse_fen(START_FEN)
        save_autoplay_state(board, side, START_FEN, [], "generic")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["saved_at"] = time.time() - 100
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(load_autoplay_state(max_age_seconds=10))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
