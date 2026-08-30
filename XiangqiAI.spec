# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path


project = Path(SPECPATH)
pikafish = Path(os.environ.get("PIKAFISH_DIR", project.parent / "Pikafish"))
engine_exe = pikafish / "Windows" / "pikafish-sse41-popcnt.exe"
engine_nnue = pikafish / "pikafish.nnue"
if not engine_exe.exists() or not engine_nnue.exists():
    raise SystemExit(
        "Set PIKAFISH_DIR to a Pikafish directory containing "
        "Windows/pikafish-sse41-popcnt.exe and pikafish.nnue"
    )

a = Analysis(
    [str(project / "app.py")],
    pathex=[str(project)],
    binaries=[(str(engine_exe), "engine")],
    datas=[
        (str(engine_nnue), "engine"),
        (str(pikafish / "Copying.txt"), "licenses/pikafish"),
        (str(pikafish / "AUTHORS"), "licenses/pikafish"),
        (str(pikafish / "NNUE-License.md"), "licenses/pikafish"),
        (str(project / "vision_models"), "vision_models"),
        (str(project / "vision_licenses"), "licenses/vision"),
        (str(project / "README.md"), "."),
        (str(project / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=["cv2", "numpy", "onnxruntime"],
    runtime_hooks=[str(project / "build_support" / "runtime_hook.py")],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XiangqiAI",
    console=False,
    upx=False,
    version=str(project / "build_support" / "version_info.txt"),
)

