"""Read-only bundled resources and persistent, per-user application data."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    configured = os.environ.get("LOCALAPPDATA", "")
    local_root = Path(configured) if configured else None
    if local_root is None or not local_root.is_absolute():
        # Never fall back to the current working directory or beside the EXE.
        local_root = Path.home() / "AppData" / "Local"
    return local_root / "XiangqiAI"
