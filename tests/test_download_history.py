import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "subtitle_tool.py"
spec = importlib.util.spec_from_file_location("subtitle_tool_download_history", MODULE_PATH)
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


class DownloadHistoryTests(unittest.TestCase):
    def test_load_keeps_only_downloads_inside_rolling_24_hours(self):
        now = 200_000.0
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "history.json"
            path.write_text(json.dumps([now - 86_401, now - 86_400, now - 1, now]), encoding="utf-8")
            self.assertEqual(tool.load_recent_downloads(path, now=now), [now - 1, now])

    def test_record_persists_success_and_updates_remaining_allowance(self):
        now = 300_000.0
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "history.json"
            history = tool.load_recent_downloads(path, now=now)
            for offset in range(9):
                tool.record_local_download(history, now=now + offset, path=path)
            loaded = tool.load_recent_downloads(path, now=now + 9)
            self.assertEqual(len(loaded), 9)
            self.assertEqual(tool.FREE_ACCOUNT_DAILY_CAP - len(loaded), 1)

    def test_tenth_download_uses_last_slot_and_old_entry_expires(self):
        now = 400_000.0
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "history.json"
            history = [now - 86_399] * 9
            tool.save_download_history(history, path)
            self.assertEqual(tool.FREE_ACCOUNT_DAILY_CAP - len(tool.load_recent_downloads(path, now=now)), 1)
            tool.record_local_download(history, now=now, path=path)
            self.assertEqual(len(tool.load_recent_downloads(path, now=now)), 10)
            self.assertEqual(len(tool.load_recent_downloads(path, now=now + 86_400)), 0)

    def test_corrupt_history_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "history.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Cannot read download history"):
                tool.load_recent_downloads(path, now=500_000)


if __name__ == "__main__":
    unittest.main()
