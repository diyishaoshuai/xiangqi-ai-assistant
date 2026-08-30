"""Real Windows single-EXE regression checks in isolated, disposable paths."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


FUNCTION_FLAGS = ["--engine-self-test", "--no-win-engine-self-test", "--anti-loop-engine-self-test",
                  "--anti-check-policy-self-test", "--outcome-guard-self-test", "--auto-analysis-self-test",
                  "--follow-best-self-test", "--follow-recovery-self-test", "--topmost-self-test",
                  "--orientation-self-test", "--direct-king-capture-self-test", "--help-self-test",
                  "--automation-self-test", "--ui-self-test"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OwnedProcess:
    def __init__(self, pid, expected_image):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        self.api.OpenProcess.restype = ctypes.c_void_p
        self.api.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        self.api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.api.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.api.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.api.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not self.api.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        if os.path.normcase(buffer.value) != os.path.normcase(str(expected_image)):
            self.close()
            raise RuntimeError("Refusing to control a process outside the test copy")
        self.pid = pid

    def terminate(self):
        if not self.api.TerminateProcess(self.handle, 99):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self):
        if self.api.WaitForSingleObject(self.handle, 20000) != 0:
            raise RuntimeError(f"Test process {self.pid} did not exit")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def close_windows(pid):
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    api.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
    api.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    api.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]

    @callback_type
    def callback(window, _data):
        owner = ctypes.c_uint32()
        api.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid:
            api.PostMessageW(window, 0x10, 0, 0)
        return 1

    api.EnumWindows(callback, 0)


def registry_snapshot():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{E81D6970-29D6-4A26-9B62-8F0AA83A1260}_is1",
                           access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return sorted(winreg.EnumValue(key, index) for index in range(winreg.QueryInfoKey(key)[1]))
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--legacy-directory", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("This integration test requires Windows.")
    project = Path(__file__).resolve().parent.parent
    output = project / "build/singlefile-verification"
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    executable_dir = root / "只读 程序"
    executable_dir.mkdir()
    executable = executable_dir / "XiangqiAI.exe"
    shutil.copy2(args.exe.resolve(), executable)
    temp = root / "临时目录"
    data = root / "用户数据"
    working = root / "空 工作目录"
    for path in (temp, data, working):
        path.mkdir()
    environment = dict(os.environ, LOCALAPPDATA=str(data), TEMP=str(temp), TMP=str(temp),
                       PIKAFISH_DIR=str(root / "missing-engine"), PYTHONPATH="", PYTHONHOME="", VIRTUAL_ENV="",
                       PATH=os.pathsep.join([str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"]]))
    started = time.time()
    results = []
    existing_registry = registry_snapshot()
    print(f"Verification directory: {root}", flush=True)

    def check(value, message):
        if not value:
            raise AssertionError(message)
        results.append(message)
        print("PASS: " + message, flush=True)

    def start(flags):
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        return subprocess.Popen([str(executable), *map(str, flags)], cwd=working, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=info,
                                creationflags=subprocess.CREATE_NO_WINDOW)

    def finish(process, name):
        out, err = process.communicate(timeout=90)
        (root / f"{name}.txt").write_bytes(out + err)
        check(process.returncode == 0, f"{name}: exit={process.returncode}")
        check(not err, f"{name}: no stderr errors")

    def clean_runtime(name):
        check(not list(temp.iterdir()), f"{name}: temporary payload removed")
        check(sorted(path.name for path in executable_dir.iterdir()) == ["XiangqiAI.exe"],
              f"{name}: executable directory contains only the EXE")
        check(not list(working.iterdir()), f"{name}: working directory untouched")

    sid = re.search(rb"S-1-5-[0-9-]+", subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"])).group().decode()
    # Restrict only our newly created test directory, never the user's actual EXE.
    assert executable_dir.resolve().is_relative_to(output.resolve())
    acl_changed = False
    try:
        subprocess.run(["icacls", str(executable_dir), "/deny", f"*{sid}:(WD,AD)"], check=True, capture_output=True)
        acl_changed = True
        try:
            (executable_dir / "must-not-be-writable.tmp").write_text("test")
        except PermissionError:
            pass
        else:
            raise AssertionError("Read-only directory fixture is not actually read-only")
        check(True, "Windows ACL denies creating files beside the EXE")
        for flag in FUNCTION_FLAGS:
            finish(start([flag]), flag[2:])
            clean_runtime(flag)

        flags = ["--runtime-probe"]
        if args.image:
            flags += ["--probe-images", *[path.resolve() for path in args.image]]
        finish(start(flags), "runtime-probe")
        report = json.loads((data / "XiangqiAI/diagnostics/runtime-probe.json").read_text(encoding="utf-8"))
        check(report["success"] and report["frozen"], "real EXE loads bundled engine, models and licenses")
        check(Path(report["resource_base"]).parent == temp, "resources extracted into configured temporary directory")
        check(Path(report["data_dir"]) == data / "XiangqiAI", "persistent data stays in per-user directory")
        clean_runtime("runtime-probe")
        if args.source_report:
            source = json.loads(args.source_report.read_text(encoding="utf-8"))
            check(report["hashes"] == source["hashes"], "bundled resources match source resources byte-for-byte")
            check(report["images"] == source["images"], "real screenshot recognition matches source output exactly")
            check(report["model_inference"] == source["model_inference"], "ONNX inference matches source output exactly")
        if args.legacy_directory:
            for relative in ("engine/pikafish-sse41-popcnt.exe", "engine/pikafish.nnue",
                             "vision_models/board_pose.onnx", "vision_models/board_classifier.onnx"):
                path = args.legacy_directory / "_internal" / relative
                check(sha256(path) == report["hashes"][path.name], f"unchanged from previous version: {path.name}")

        for mode in ("graceful", "forced-child-exit"):
            marker = data / "XiangqiAI/diagnostics/runtime-smoke.json"
            if marker.exists():
                marker.unlink()
            process = start(["--runtime-smoke-session", "--seed-uninstall-data", "--uninstall-smoke-thinking"])
            deadline = time.monotonic() + 45
            while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.1)
            check(marker.exists(), f"{mode}: hidden GUI and thinking engine are ready")
            state = json.loads(marker.read_text(encoding="utf-8"))
            payload = Path(state["resource_base"])
            with ImageFreeProcesses(state, executable, payload) as (worker, engine):
                if mode == "graceful":
                    close_windows(worker.pid)
                else:
                    worker.terminate()
                worker.wait()
                engine.wait()
                check(True, f"{mode}: both GUI and engine terminate")
            process.communicate(timeout=30)
            check(process.returncode == (0 if mode == "graceful" else 99), f"{mode}: launcher returns expected exit status")
            clean_runtime(mode)
            check((data / "XiangqiAI/recognition_templates.json").is_file(), f"{mode}: learned data persists after exit")
            check((data / "XiangqiAI/logs/xiangqi-ai.log").is_file(), f"{mode}: diagnostic logs persist after exit")

        first, second = start(["--log-stress-self-test"]), start(["--log-stress-self-test"])
        finish(first, "concurrent-log-1")
        finish(second, "concurrent-log-2")
        clean_runtime("concurrent logging")
        logs = list((data / "XiangqiAI/logs").glob("xiangqi-ai.log*"))
        check(len(logs) == 4, "automatic rotation retains current log plus three backups")
        check(sum(path.stat().st_size for path in logs) <= 8 * 1024 * 1024, "combined log size stays within 8 MiB")
        check(registry_snapshot() == existing_registry, "no installer registry entry created or changed")
        check(sha256(executable) == sha256(args.exe.resolve()), "single EXE is unchanged by running it")
        result = {"success": True, "assertions": len(results), "checks": results,
                  "elapsed_seconds": round(time.time() - started, 2), "exe_sha256": sha256(executable),
                  "exe_bytes": executable.stat().st_size, "runtime": report}
        (root / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ALL PASSED: {len(results)} assertions. Report: {root / 'verification.json'}", flush=True)
    finally:
        if acl_changed:
            subprocess.run(["icacls", str(executable_dir), "/remove:d", f"*{sid}"], check=True, capture_output=True)


class ImageFreeProcesses:
    """Retain verified native handles so PID reuse cannot redirect termination."""
    def __init__(self, state, executable, payload):
        self.worker = OwnedProcess(state["pid"], executable)
        self.engine = OwnedProcess(state["engine_pid"], payload / "engine/pikafish-sse41-popcnt.exe")

    def __enter__(self):
        return self.worker, self.engine

    def __exit__(self, *_args):
        self.worker.close()
        self.engine.close()


if __name__ == "__main__":
    main()
