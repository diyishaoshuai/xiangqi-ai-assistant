"""Replay an existing game screenshot and time the click path (NO real input)."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from app import XiangqiApp, find_engine
from automation import click_screen_move
from core import make_fen, parse_move
from engine import PikafishEngine
from recognition import PieceRecognizer
from screen_cache import UnchangedBoardCache
from test_automation import FakeUser32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    image = Image.open(args.image).convert("RGB")
    recognizer = PieceRecognizer()
    grid, detections = recognizer.recognize(image)
    board = {item.square: item.piece for item in detections}
    assert None not in board.values() and "K" in board.values() and "k" in board.values()
    geometry = recognizer.last_geometry
    assert geometry is not None

    baseline_start = time.monotonic()
    for frame in range(3):
        recognizer.recognize(image.copy(), geometry_hint=geometry,
                             minimum_geometry_confidence=.1, cancelled=lambda: False)
        if frame < 2:
            time.sleep(.30)
    baseline_ms = (time.monotonic() - baseline_start) * 1000

    app = object.__new__(XiangqiApp)
    app.closing, app.mouse_auto_session_id = False, 1
    app.mouse_auto_stop_event = threading.Event()
    app.mouse_auto_frame_cache = UnchangedBoardCache()
    app.mouse_auto_frame_cache.remember(image, board, grid, geometry)
    app.logger = logging.getLogger("benchmark")
    engine = PikafishEngine(find_engine(), threads=2, hash_mb=64)
    samples = []
    try:
        engine.start()  # One-time engine startup is separate from move latency.
        for _ in range(5):
            started = time.monotonic()
            _, move = engine.analyse(make_fen(board, "w"), 500, 1)
            thought = time.monotonic()
            with patch("app.ImageGrab.grab", side_effect=image.copy), patch("app.foreground_window", return_value=42), patch("app.user_input_is_idle", return_value=True):
                kind, fresh = app._wait_for_click_ready(1, app.mouse_auto_stop_event, 42, board, {}, {})
            assert kind == "ready"
            ready = time.monotonic()
            start, end = parse_move(move)
            cursor = FakeUser32([True, True])
            click_screen_move(fresh[2].point_for_square(start), fresh[2].point_for_square(end),
                              lambda: False, user32=cursor)
            assert len(cursor.mouse_events) == 4
            samples.append({"think_ms": round((thought - started) * 1000, 2),
                            "after_think_to_click_ready_ms": round((ready - thought) * 1000, 2),
                            "after_think_to_both_clicks_ms": round((time.monotonic() - thought) * 1000, 2)})
    finally:
        engine.close()
    report = {"fixture_size": image.size, "budget_ms": 500, "real_mouse_input": False,
              "note": "Screenshot replay replaces OS capture with an in-memory copy; no live game or network delay included.",
              "previous_three_model_verifications_ms": round(baseline_ms, 2),
              "fast_verification_median_ms": statistics.median(item["after_think_to_click_ready_ms"] for item in samples),
              "after_think_to_both_clicks_median_ms": statistics.median(item["after_think_to_both_clicks_ms"] for item in samples),
              "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
