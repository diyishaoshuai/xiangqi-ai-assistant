"""Replay a real two-ply image transition through recognition, 500ms search and simulated clicks."""
import argparse
import json
import logging
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from app import XiangqiApp, find_engine
from automation import click_screen_move
from core import apply_move, make_fen, parse_move
from engine import PikafishEngine
from recognition import PieceRecognizer
from screen_cache import UnchangedBoardCache
from search_pipeline import ConfirmedSearch
from test_automation import FakeUser32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--moves", nargs=2, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before_image, after_image = (Image.open(path).convert("RGB") for path in (args.before, args.after))
    recognizer = PieceRecognizer()
    _, detections = recognizer.recognize(after_image)
    after = {item.square: item.piece for item in detections}
    grid, detections = recognizer.recognize(before_image)
    before = {item.square: item.piece for item in detections}
    geometry = recognizer.last_geometry
    assert apply_move(apply_move(before, args.moves[0]), args.moves[1]) == after
    engine = PikafishEngine(find_engine(), threads=8)
    samples = []
    try:
        engine.start()
        for overlap in (False, True, False, True):
            app = object.__new__(XiangqiApp)
            app.closing, app.mouse_auto_session_id = False, 1
            stop = threading.Event()
            app.recognizer, app.logger = recognizer, logging.getLogger("pipeline-benchmark")
            app._queue_mouse_status, app._log_mouse_recovery = Mock(), Mock()
            app.mouse_auto_frame_cache = UnchangedBoardCache()
            app.mouse_auto_frame_cache.remember(before_image, before, grid, geometry)
            app.mouse_auto_geometry = geometry
            prefetch = ConfirmedSearch(engine)
            key = prefetch.request_key(make_fen(after, "w"), 500, 1, make_fen(before, "w"), args.moves)
            captures = 0

            def grab():
                nonlocal captures
                captures += 1
                frame = after_image.copy()
                if captures == 1:
                    # Force second-frame reclassification instead of benchmarking
                    # only identical-pixel reuse. One background RGB pixel differs.
                    bounds = app.mouse_auto_frame_cache.bounds
                    p = (bounds[0] + 2, bounds[1] + 2)
                    rgb = frame.getpixel(p)
                    frame.putpixel(p, ((rgb[0] + 1) % 256, rgb[1], rgb[2]))
                return frame

            def candidate(board):
                assert board == after
                prefetch.offer(key)

            started = time.perf_counter()
            try:
                with patch("app.ImageGrab.grab", side_effect=grab), patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True):
                    board, _, _ = app._capture_stable_mouse_board(1, stop, stable_frames=2,
                        accept=lambda item: item == after, on_candidate=candidate if overlap else None)
                    confirmed = time.perf_counter()
                    result = prefetch.take(key, stop.is_set) if overlap else None
                    if result is None:
                        result = engine.analyse(key[0], 500, 1, history_fen=key[3], moves=key[4])
                    thought = time.perf_counter()
                    kind, fresh = app._wait_for_click_ready(1, stop, 42, board, before, {})
                    assert kind == "ready"
                    a, b = parse_move(result[1])
                    fake_cursor = FakeUser32([True, True])
                    click_screen_move(fresh[2].point_for_square(a), fresh[2].point_for_square(b),
                        stop.is_set, pause_seconds=.10, settle_seconds=.03, user32=fake_cursor)
                samples.append({"overlap": overlap, "two_frame_confirmation_ms": round((confirmed-started)*1000, 1),
                    "engine_wait_after_confirmation_ms": round((thought-confirmed)*1000, 1),
                    "transition_to_both_clicks_ms": round((time.perf_counter()-started)*1000, 1),
                    "captured_frames": captures, "real_mouse_input": False})
            finally:
                prefetch.close()
    finally:
        engine.close()
    report = {"note": "Real image transition/models/engine; screenshots, human-idle guard and mouse are simulated. No game/network response included.", "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
