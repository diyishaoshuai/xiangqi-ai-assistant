"""Verify source-image colour inference with real ONNX, without screen/input access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from player_side import bottom_player_side
from recognition import PieceRecognizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--expected", choices=("w", "b"), default="w")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    recognizer = PieceRecognizer()
    original = Image.open(args.image).convert("RGB")
    cases = [("original", original, args.expected),
             ("rotated", original.transpose(Image.Transpose.ROTATE_180), "b" if args.expected == "w" else "w"),
             ("scaled", original.resize((1280, 720)), args.expected)]
    baseline = None
    samples = []
    for name, image, expected in cases:
        _, detections = recognizer.recognize(image)
        board = {item.square: item.piece for item in detections if item.piece}
        confidences = {item.piece: item.confidence for item in detections if item.piece in ("K", "k")}
        actual = bottom_player_side(board, recognizer.last_geometry, king_confidences=confidences)
        assert recognizer.last_backend == "onnx", recognizer.last_error
        assert actual == expected, (name, actual, expected, confidences)
        if baseline is None:
            baseline = board
        assert board == baseline, (name, "canonical position changed")
        samples.append({"case": name, "size": image.size, "expected": expected,
                        "detected": actual, "king_confidences": confidences, "pieces": len(board)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"success": True, "samples": samples}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
