"""Bounded, offline CPU benchmark. Never attaches to a running game/engine."""
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import PikafishEngine
from core import START_FEN
from app import find_engine


def main():
    positions = [START_FEN,
        "2bakcb2/2cR5/n8/C5p2/8p/2p3P2/P3r3P/4B4/4A4/3A1KB2 w - - 0 1",
        "3aka3/8r/cR2bnn2/p1p1p1p1p/2b6/P4NP2/4P3P/N1C1B4/9/2BAKA3 b - - 0 1"]
    jobs = [(t, h, i, rep) for t in (8, 12, 14) for h in (256, 1024, 2048)
            for i in range(3) for rep in range(2)]
    random.Random(19).shuffle(jobs)
    rows = []
    for threads, cache, index, rep in jobs:
        engine = PikafishEngine(find_engine(), threads=threads, hash_mb=cache)
        try:
            engine.start()
            engine._send(f"position fen {positions[index]}")
            started = time.perf_counter()
            engine._send("go movetime 1000")
            output = engine._read_until("bestmove")
            info = next((line for line in reversed(output) if " nodes " in line), "")
            nodes = re.search(r"\bnodes (\d+)", info)
            depth = re.search(r"\bdepth (\d+)", info)
            row = dict(threads=threads, hash_mb=cache, position=index, repeat=rep,
                       nodes=int(nodes[1]) if nodes else 0, depth=int(depth[1]) if depth else 0,
                       elapsed_ms=round((time.perf_counter()-started)*1000, 1))
            rows.append(row)
            print(json.dumps(row), flush=True)
        finally:
            engine.close()
    summary = [dict(threads=t, hash_mb=h,
                    median_nodes=statistics.median(r['nodes'] for r in rows if r['threads']==t and r['hash_mb']==h))
               for t in (8,12,14) for h in (256,1024,2048)]
    path = Path("build/engine-resource-benchmark.json")
    path.write_text(json.dumps(dict(rows=rows, summary=summary), indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
