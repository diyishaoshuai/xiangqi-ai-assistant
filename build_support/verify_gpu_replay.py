"""Offline CPU/GPU full recognition comparison; does not capture the screen."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from recognition import PieceRecognizer

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
args = parser.parse_args()
images = [Image.open(path).convert('RGB') for path in sorted(args.directory.glob('frame-*.png'))]
results = {}
for device in (-1,0):
    with patch('vision_runtime.resource_settings', return_value={'vision_device':device}):
        recognizer = PieceRecognizer()
        recognizer.recognize(images[0])
        rows = []
        for index,image in enumerate(images):
            start = time.perf_counter()
            try:
                grid, detections = recognizer.recognize(image)
                row = dict(frame=index, ms=round((time.perf_counter()-start)*1000,2),
                           board=sorted((str(d.square),d.piece) for d in detections))
            except Exception as exc:
                row = dict(frame=index,error=str(exc))
            rows.append(row)
        results[str(device)] = rows
        print(json.dumps({'device':device,'rows':rows}),flush=True)
matches = bool(images) and all('board' in a and 'board' in b and a['board'] == b['board']
              for a,b in zip(results['-1'],results['0']))
report = dict(matches=matches,results=results)
Path('build/gpu-replay.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('MATCHES',matches,flush=True)
if not matches: raise SystemExit(1)
