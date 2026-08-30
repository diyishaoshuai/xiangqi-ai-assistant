"""PyInstaller runtime hook: no disk bytecode, no relaunch during uninstall."""
import os
import sys

from maintenance import installation_in_maintenance

sys.dont_write_bytecode = True
if installation_in_maintenance(os.path.dirname(sys.executable)):
    raise SystemExit(0)
