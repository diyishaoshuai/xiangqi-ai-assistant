import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_paths import resource_base, user_data_dir
from diagnostics import LOG_BACKUP_COUNT, LOG_MAX_BYTES, SharedRotatingFileHandler, configure_logging
from recognition import TemplatePieceRecognizer


class StorageTests(unittest.TestCase):
    def test_resources_use_extraction_directory_not_executable_directory(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "_MEIPASS", r"C:\Temp\_MEI-test", create=True
        ), patch.object(sys, "executable", r"D:\Somewhere\XiangqiAI.exe"):
            self.assertEqual(resource_base(), Path(r"C:\Temp\_MEI-test"))

    def test_data_never_falls_back_to_working_or_executable_directory(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"LOCALAPPDATA": ""}), patch("app_paths.Path.home", return_value=Path(home)):
                self.assertEqual(user_data_dir(), Path(home) / "AppData" / "Local" / "XiangqiAI")
            with patch.dict(os.environ, {"LOCALAPPDATA": "relative-dir"}), patch("app_paths.Path.home", return_value=Path(home)):
                self.assertEqual(user_data_dir(), Path(home) / "AppData" / "Local" / "XiangqiAI")

    def test_existing_learning_is_retained_in_user_directory(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {"LOCALAPPDATA": data}):
            first = TemplatePieceRecognizer()
            first.learn("R", [0.0] * 256)
            content = first.learned_path.read_bytes()
            second = TemplatePieceRecognizer()
            self.assertEqual(second.learned_path, Path(data) / "XiangqiAI" / "recognition_templates.json")
            self.assertEqual(len(second.templates["R"]), 1)
            self.assertEqual(second.learned_path.read_bytes(), content)

    def test_legacy_learning_fallback_is_loaded_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"LOCALAPPDATA": data}):
                first = TemplatePieceRecognizer()
                first.learn("R", [0.0] * 256)
                legacy = Path(home) / ".xiangqi_ai" / "recognition_templates.json"
                legacy.parent.mkdir()
                first.learned_path.rename(legacy)
                with patch("recognition.Path.home", return_value=Path(home)):
                    second = TemplatePieceRecognizer()
                    self.assertEqual(len(second.templates["R"]), 1)
                    second.learn("R", [0.0] * 256)
                    self.assertTrue(second.learned_path.exists())
                    self.assertTrue(legacy.exists())

    def test_log_setup_is_persistent_idempotent_and_bounded(self):
        logger = logging.getLogger("xiangqi_ai")
        previous = list(logger.handlers)
        logger.handlers = []
        try:
            with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {"LOCALAPPDATA": data}):
                path = configure_logging()
                logger.info("session one")
                self.assertEqual(configure_logging(), path)
                self.assertEqual(len(logger.handlers), 1)
                handler = logger.handlers[0]
                self.assertEqual(handler.maxBytes, 2 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 3)
                handler.close()
                logger.handlers = []
                configure_logging()
                logger.info("session two")
                self.assertIn("session one", path.read_text(encoding="utf-8"))
                self.assertIn("session two", path.read_text(encoding="utf-8"))
                for handler in logger.handlers:
                    handler.close()
                logger.handlers = []
        finally:
            logger.handlers = previous

    def test_rotation_keeps_four_files_and_releases_file_handles(self):
        with tempfile.TemporaryDirectory() as data:
            path = Path(data) / "xiangqi-ai.log"
            handler = SharedRotatingFileHandler(path, maxBytes=1024, backupCount=3)
            try:
                for index in range(100):
                    handler.handle(logging.makeLogRecord({"msg": f"{index}:" + "x" * 120}))
                self.assertIsNone(handler.stream)
                files = list(Path(data).glob("xiangqi-ai.log*"))
                self.assertEqual(len(files), 4)
                self.assertLessEqual(sum(file.stat().st_size for file in files), 4096)
                renamed = path.with_suffix(".renamed")
                path.rename(renamed)  # No live stream blocking a concurrent rollover.
            finally:
                handler.close()
            self.assertEqual(LOG_MAX_BYTES * (LOG_BACKUP_COUNT + 1), 8 * 1024 * 1024)

    @unittest.skipUnless(os.name == "nt", "Windows multi-process log serialization")
    def test_two_processes_can_rotate_the_same_logs(self):
        code = (
            "import logging,sys; from pathlib import Path; "
            "from diagnostics import SharedRotatingFileHandler; "
            "h=SharedRotatingFileHandler(Path(sys.argv[1]), maxBytes=1024, backupCount=3); "
            "[h.handle(logging.makeLogRecord({'msg':str(i)+':'+'x'*120})) for i in range(200)]; "
            "h.close()"
        )
        with tempfile.TemporaryDirectory() as data:
            path = Path(data) / "xiangqi-ai.log"
            processes = [subprocess.Popen([sys.executable, "-c", code, str(path)],
                         stderr=subprocess.PIPE, stdout=subprocess.PIPE) for _ in range(2)]
            for process in processes:
                _out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, err.decode(errors="replace"))
                self.assertEqual(err, b"")
            files = list(Path(data).glob("xiangqi-ai.log*"))
            self.assertEqual(len(files), 4)
            self.assertLessEqual(sum(file.stat().st_size for file in files), 4096)


if __name__ == "__main__":
    unittest.main()
