"""Compare real model inference on GUI/main and autoplay/background threads. No input injection."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import onnxruntime as ort
from PIL import Image
from recognition import PieceRecognizer, extract_chessboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    image = Image.open(args.image).convert("RGB")
    recognizer = PieceRecognizer()
    recognizer.recognize(image)
    n = recognizer.neural
    keypoints = np.float32([recognizer.last_geometry.point_for_square(square)
                           for square in ((0, 9), (8, 9), (0, 0), (8, 0))])
    transformed, _, _ = extract_chessboard(np.asarray(image), keypoints)
    tensor = n.classifier.preprocess_image(transformed)
    reference = n.classifier.run_inference(tensor)
    rows = []
    for threads, flush_denormals in ((2, False), (4, False), (2, True), (4, True)):
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.set_denormal_as_zero", "1" if flush_denormals else "0")
        model = ort.InferenceSession("vision_models/board_classifier.onnx", sess_options=options)

        def run(label):
            values = []
            for _ in range(3):
                started = time.perf_counter()
                result, = model.run(None, {model.get_inputs()[0].name: tensor})
                values.append(round((time.perf_counter() - started) * 1000, 1))
            row = {"threads": threads, "flush_denormals": flush_denormals, "caller": label,
                   "median_ms": statistics.median(values), "samples_ms": values,
                   "labels_equal": bool(np.array_equal(reference.argmax(-1), result.argmax(-1))),
                   "max_score_difference": float(np.max(np.abs(reference - result)))}
            rows.append(row)
            print(json.dumps(row), flush=True)

        run("main")
        worker = threading.Thread(target=run, args=("autoplay-worker",))
        worker.start()
        worker.join()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
