"""Installation-scoped maintenance guard shared with the Windows installer."""
from __future__ import annotations

import ctypes
import hashlib
import ntpath
import os
from pathlib import Path


def maintenance_event_name(install_dir: str | Path) -> str:
    # Inno Setup's GetSHA256OfUnicodeString hashes UTF-16LE, without a BOM.
    normalized = ntpath.normpath(str(install_dir)).lower()
    digest = hashlib.sha256(normalized.encode("utf-16le")).hexdigest()
    return "Local\\XiangqiAI.Maintenance." + digest


def installation_in_maintenance(install_dir: str | Path) -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenEventW(0x00100000, False, maintenance_event_name(install_dir))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel32.CloseHandle(handle)
