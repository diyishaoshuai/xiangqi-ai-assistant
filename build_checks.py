"""Opt-in offline verification commands; never run during normal gameplay."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import sys
import traceback
import unittest

from app_paths import resource_base, user_data_dir
from diagnostics import APP_VERSION, configure_logging


def save_report(name, report):
    directory = user_data_dir() / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def runtime_probe(app):
    import numpy as np
    from PIL import Image

    configure_logging()
    base = resource_base()
    report = {"version": APP_VERSION, "pid": os.getpid(), "frozen": bool(getattr(sys, "frozen", False)),
              "resource_base": str(base), "executable": sys.executable, "data_dir": str(user_data_dir())}
    try:
        network = app.find_engine().parent / "pikafish.nnue"
        if not network.exists():
            network = app.find_engine().parent.parent / "pikafish.nnue"
        required = [app.find_engine(), network,
                    base / "vision_models/board_pose.onnx", base / "vision_models/board_classifier.onnx",
                    base / "README.md", base / "THIRD_PARTY_NOTICES.md",
                    base / "licenses/pikafish/Copying.txt" if report["frozen"] else app.find_engine().parent.parent / "Copying.txt"]
        hashes = {}
        for path in required:
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        report["hashes"] = hashes
        recognizer = app.PieceRecognizer()
        neural = recognizer._ensure_neural()
        blank = np.zeros((500, 500, 3), dtype=np.uint8)
        keypoints, scores = neural.pose.pred(image=blank, bbox=[0, 0, 500, 500])
        _, rows, confidences, layout = neural.classifier.pred(blank, is_rgb=True)
        assert keypoints.shape == (4, 2) and np.isfinite(keypoints).all()
        assert np.isfinite(scores).all() and np.isfinite(confidences).all()
        assert len(rows) == 10 and all(len(row) == 9 for row in rows)
        report["model_inference"] = {"layout": layout, "keypoints": keypoints.tolist(), "scores": [float(x) for x in scores]}
        report["images"] = {}
        if "--probe-images" in sys.argv:
            for filename in sys.argv[sys.argv.index("--probe-images") + 1:]:
                with Image.open(filename) as image:
                    _, detections = recognizer.recognize(image)
                assert recognizer.last_backend == "onnx", recognizer.last_error
                assert detections and recognizer.last_geometry is not None
                report["images"][os.path.basename(filename)] = {
                    "pieces": sorted([[list(item.square), item.piece] for item in detections]),
                    "layout": recognizer.neural.last_layout,
                    "backend": recognizer.last_backend,
                }
        report["success"] = True
        return 0
    except Exception:
        report["success"] = False
        report["error"] = traceback.format_exc()
        logging.getLogger("xiangqi_ai").exception("runtime verification failed")
        return 70
    finally:
        save_report("runtime-probe.json", report)


def automation_probe():
    # These suites mock Win32 input: running them never clicks a real board.
    import test_automation
    import test_app_automation
    import test_app_logic

    stream = io.StringIO()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module)
                              for module in (test_automation, test_app_automation, test_app_logic))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    save_report("automation-probe.json", {"success": result.wasSuccessful(),
                "tests": result.testsRun, "output": stream.getvalue()})
    return 0 if result.wasSuccessful() else 71


def log_stress_probe():
    configure_logging()
    logger = logging.getLogger("xiangqi_ai.verification")
    for number in range(4500):
        logger.debug("rotation-check %s %s", number, "x" * 1100)
    return 0


def ui_probe():
    import test_ui

    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_ui)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    save_report("ui-probe.json", {"success": result.wasSuccessful(), "tests": result.testsRun,
                                 "output": stream.getvalue()})
    return 0 if result.wasSuccessful() else 72


def help_probe(app):
    root = app.tk.Tk()
    root.withdraw()
    instance = None
    try:
        instance = app.XiangqiApp(root)
        instance.auto_analysis_var.set(False)
        instance._invalidate_analysis()
        for licenses in (False, True):
            window = instance.show_help_document(licenses)
            window.withdraw()
            text = next(child for child in window.winfo_children() if isinstance(child, app.tk.Text))
            content = text.get("1.0", "end")
            assert ("GNU GENERAL PUBLIC LICENSE" if licenses else "F1") in content
            window.destroy()
        return 0
    finally:
        if instance is not None:
            instance._on_close()
        else:
            root.destroy()
