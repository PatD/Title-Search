import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "subtitle_tool.py"
spec = importlib.util.spec_from_file_location("subtitle_tool", MODULE_PATH)
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)


class ResilienceTests(unittest.TestCase):
    def test_get_request_retries_service_error_then_succeeds(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        response.__enter__.return_value.headers = {}
        failure = urllib.error.HTTPError("url", 503, "busy", {}, io.BytesIO())
        with mock.patch.object(tool.urllib.request, "urlopen", side_effect=[failure, response]), \
             mock.patch.object(tool.time, "sleep") as sleep:
            body, _ = tool.request("https://example.invalid")
        self.assertEqual(body, b"ok")
        sleep.assert_called_once_with(1)

    def test_post_request_is_not_retried(self):
        failure = urllib.error.HTTPError("url", 503, "busy", {}, io.BytesIO())
        with mock.patch.object(tool.urllib.request, "urlopen", side_effect=failure) as open_url:
            with self.assertRaises(tool.ApiFailure):
                tool.request("https://example.invalid", method="POST", data=b"{}")
        self.assertEqual(open_url.call_count, 1)

    def test_retries_timeouts_only_three_times(self):
        with mock.patch.object(tool.urllib.request, "urlopen", side_effect=TimeoutError("late")) as open_url, \
             mock.patch.object(tool.time, "sleep") as sleep:
            with self.assertRaises(tool.ApiFailure) as raised:
                tool.request("https://example.invalid")
        self.assertTrue(raised.exception.outage)
        self.assertEqual(open_url.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_corrupt_mp4_is_reported(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "bad.mp4"
            path.write_bytes(b"not an mp4")
            with mock.patch.object(tool.shutil, "which", return_value=None):
                with self.assertRaises(tool.MediaProbeError):
                    tool.probe_media(path)

    def test_ffprobe_failure_is_reported_as_corrupt_media(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            path = Path(directory) / "bad.mkv"
            path.write_bytes(b"broken")
            failure = subprocess.CalledProcessError(1, "ffprobe", stderr="Invalid data found")
            with mock.patch.object(tool.shutil, "which", return_value="ffprobe"), \
                 mock.patch.object(tool.subprocess, "run", side_effect=failure):
                with self.assertRaisesRegex(tool.MediaProbeError, "Invalid data found"):
                    tool.probe_media(path)

    def test_dependency_preflight_checks_whisper_and_ffmpeg(self):
        with mock.patch.object(tool.importlib.util, "find_spec", return_value=None), \
             mock.patch.object(tool.shutil, "which", return_value=None):
            problems = tool.preflight(transcribe_missing=True)
        self.assertTrue(any("Whisper" in problem for problem in problems))
        self.assertTrue(any("FFmpeg" in problem for problem in problems))

    def test_dependency_preflight_keeps_optional_dependencies_optional(self):
        with mock.patch.object(tool.importlib.util, "find_spec", return_value=None), \
             mock.patch.object(tool.shutil, "which", return_value=None):
            self.assertEqual(tool.preflight(transcribe_missing=False), [])

    def test_srt_checks_cues_order_and_runtime(self):
        good = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
        bad_order = b"1\n00:00:03,000 --> 00:00:04,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nWorld\n"
        self.assertTrue(tool.valid_srt(good, duration=10))
        self.assertFalse(tool.valid_srt(bad_order))
        self.assertFalse(tool.valid_srt(good, duration=0.1))

    def test_bad_subtitle_zip_becomes_clear_validation_error(self):
        with self.assertRaisesRegex(ValueError, "archive is damaged"):
            tool.srt_payload(b"PK\x03\x04garbage")

    def test_scanning_skips_broken_symlinks_and_records_permission_errors(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            root = Path(directory)
            (root / "unreadable.mp4").write_bytes(b"media")
            with mock.patch.object(Path, "is_file", side_effect=PermissionError("denied")):
                videos, warnings = tool.scan_videos(root, recursive=True)
        self.assertEqual(videos, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Could not inspect", warnings[0])

    def test_output_failure_does_not_create_outside_target(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory:
            movie = Path(directory) / "movie.mp4"
            movie.write_bytes(b"media")
            with mock.patch.object(tool.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read only")), \
                 mock.patch.object(tool.os, "open", side_effect=PermissionError("read only")):
                with self.assertRaises(PermissionError):
                    tool.write_subtitle(movie, b"sample")
            self.assertFalse((Path(directory) / "movie.en.srt").exists())

    def test_no_path_prompts_then_exits_when_input_is_blank(self):
        with mock.patch.object(sys, "argv", [str(MODULE_PATH)]), \
             mock.patch.object(tool, "console", None), \
             mock.patch("builtins.input", return_value="") as prompt, \
             mock.patch.object(tool, "show") as show:
            result = tool.main()
        self.assertEqual(result, 0)
        prompt.assert_called_once()
        show.assert_called_once_with("No folder path provided. Exiting.", style="yellow")

    def test_folder_prompt_uses_rich_console_when_available(self):
        rich_console = mock.Mock()
        rich_console.input.return_value = "C:\\Movies"
        with mock.patch.object(tool, "console", rich_console):
            result = tool.prompt_for_folder()
        self.assertEqual(result, "C:\\Movies")
        rich_console.print.assert_called_once()
        rich_console.input.assert_called_once()

    def test_interactive_folder_with_no_videos_reports_empty_library(self):
        with tempfile.TemporaryDirectory(dir=MODULE_PATH.parent / "tests") as directory, \
             mock.patch.object(sys, "argv", [str(MODULE_PATH)]), \
             mock.patch.object(tool, "console", None), \
             mock.patch("builtins.input", return_value=directory), \
             mock.patch.object(tool, "show") as show:
            result = tool.main()
        self.assertEqual(result, 0)
        self.assertTrue(any(
            "No supported video files found" in call.args[0]
            for call in show.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
