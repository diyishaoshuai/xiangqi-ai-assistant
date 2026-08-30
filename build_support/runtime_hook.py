"""Run before third-party imports in the frozen, single-file application."""
import os
import sys

sys.dont_write_bytecode = True

from maintenance import installation_in_maintenance

# Preserve protection if someone launches a copy inside a legacy installation.
if installation_in_maintenance(os.path.dirname(sys.executable)):
    raise SystemExit(0)

from diagnostics import configure_logging, install_exception_logging

configure_logging()
install_exception_logging()
