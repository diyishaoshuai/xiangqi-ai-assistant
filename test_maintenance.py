import ctypes
import hashlib
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from engine import EngineError, PikafishEngine
from maintenance import installation_in_maintenance, maintenance_event_name


class MaintenanceTests(unittest.TestCase):
    def test_event_name_matches_inno_utf16_and_normalizes_windows_path(self):
        path = r"C:\Tools\象棋 AI"
        expected = hashlib.sha256(path.lower().encode("utf-16le")).hexdigest()
        self.assertEqual(
            maintenance_event_name(path + "\\"),
            "Local\\XiangqiAI.Maintenance." + expected,
        )
        self.assertEqual(
            maintenance_event_name(r"C:\TOOLS\象棋 AI"),
            maintenance_event_name(path),
        )

    @unittest.skipUnless(os.name == "nt", "Windows named events")
    def test_maintenance_guard_only_blocks_the_matching_installation(self):
        target = fr"C:\XiangqiAI-UnitTest-{os.getpid()}"
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        api.CreateEventW.restype = ctypes.c_void_p
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        api.CloseHandle.restype = ctypes.c_int
        handle = api.CreateEventW(None, True, True, maintenance_event_name(target))
        self.assertTrue(handle)
        try:
            self.assertTrue(installation_in_maintenance(target))
            self.assertFalse(installation_in_maintenance(target + "-other"))
        finally:
            api.CloseHandle(handle)
        self.assertFalse(installation_in_maintenance(target))


class EngineShutdownTests(unittest.TestCase):
    def test_closing_pipe_reports_engine_error_not_value_error(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.process = Mock()
        engine.process.stdin.write.side_effect = ValueError("closed pipe")
        with self.assertRaises(EngineError):
            engine._send("stop")
        engine.process.stdout.readline.side_effect = ValueError("closed pipe")
        with self.assertRaises(EngineError):
            engine._read_until("readyok")

    def test_inflight_reader_survives_detached_process(self):
        engine = PikafishEngine(Path("unused.exe"))
        process = Mock()
        engine.process = process

        def read_and_detach():
            engine.process = None
            process.stdout.readline.side_effect = None
            process.stdout.readline.return_value = "readyok\n"
            return "id name Pikafish\n"

        process.stdout.readline.side_effect = read_and_detach
        self.assertEqual(engine._read_until("readyok"), ["id name Pikafish", "readyok"])

    def test_closed_engine_cannot_spawn_a_late_analysis_process(self):
        engine = PikafishEngine(Path("unused.exe"))
        engine.close()
        with patch("engine.subprocess.Popen") as spawn:
            with self.assertRaises(EngineError):
                engine.start()
            spawn.assert_not_called()

    def test_quit_waits_and_releases_pipes(self):
        engine = PikafishEngine(Path("unused.exe"))
        process = Mock()
        process.poll.return_value = None
        engine.process = process
        engine.close()
        process.stdin.write.assert_called_once_with("quit\n")
        process.wait.assert_called_once_with(timeout=1.5)
        process.stdin.close.assert_called_once()
        process.stdout.close.assert_called_once()
        process.terminate.assert_not_called()
        engine.close()
        process.wait.assert_called_once()

    def test_hung_engine_is_terminated_and_reaped(self):
        engine = PikafishEngine(Path("unused.exe"))
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("unused", 1.5), 0]
        engine.process = process
        engine.close()
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        process.stdout.close.assert_called_once()

    def test_kill_fallback_is_also_waited_for(self):
        engine = PikafishEngine(Path("unused.exe"))
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired("unused", 1.5),
            subprocess.TimeoutExpired("unused", 1.5),
            0,
        ]
        engine.process = process
        engine.close()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 3)


if __name__ == "__main__":
    unittest.main()
