"""Compare full pose/classifier outputs and latency on saved board crops only."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--runtime', type=Path, required=True)
parser.add_argument('--image', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.runtime.resolve()))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
import numpy as np
import onnxruntime as ort
from PIL import Image
from recognition import PieceRecognizer

image = Image.open(args.image).convert('RGB')
with patch('vision_runtime.resource_settings', return_value={'vision_device': -1}):
    recognizer = PieceRecognizer()
    recognizer.recognize(image)
models = [recognizer.neural.pose, recognizer.neural.classifier]
# Capture real input tensors through a session wrapper.
feeds = []
class Capture:
    def __init__(self, session): self.session = session
    def run(self, names, feed):
        feeds.append((self.session, {k: v.copy() for k, v in feed.items()}))
        return self.session.run(names, feed)
    def __getattr__(self, name): return getattr(self.session, name)
for model in models: model.session = Capture(model.session)
recognizer.recognize(image)
rows = []
for reference, feed in feeds[:2]:
    baseline = reference.run(None, feed)
    for device in ('cpu', '0', '1'):
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 6
            options.inter_op_num_threads = 1
            options.enable_mem_pattern = False
            options.add_session_config_entry('session.intra_op.allow_spinning', '0')
            providers = ['CPUExecutionProvider'] if device == 'cpu' else [('DmlExecutionProvider', {'device_id': device}), 'CPUExecutionProvider']
            session = ort.InferenceSession(reference._model_path, sess_options=options, providers=providers)
            samples = []
            for i in range(12):
                started = time.perf_counter()
                output = session.run(None, feed)
                if i >= 2: samples.append((time.perf_counter()-started)*1000)
            row = dict(model=Path(reference._model_path).name, device=device,
                       providers=session.get_providers(), median_ms=statistics.median(samples),
                       max_diff=max(float(np.max(np.abs(a-b))) for a,b in zip(baseline,output)),
                       same_argmax=all(np.array_equal(a.argmax(-1),b.argmax(-1)) for a,b in zip(baseline,output)))
        except Exception as exc:
            row = dict(device=device, error=str(exc))
        rows.append(row)
        print(json.dumps(row), flush=True)
Path('build/gpu-benchmark.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
