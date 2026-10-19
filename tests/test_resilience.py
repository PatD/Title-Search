import importlib.util
import io
import json
import os
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
    def test_tui_summary_emits_machine_readable_line_only_for_ui_child(self):
        summary = {"videos_found": 2, "ignored_existing": 2}
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"TITLE_SEARCH_TUI_CHILD": "1"}), \
             mock.patch("sys.stdout", output):
            tool.emit_tui_summary(summary)
        self.assertEqual(
            output.getvalue(),
            "TITLE_SEARCH_SUMMARY:{\"videos_found\":2,\"ignored_existing\":2}\n",
        )

        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("sys.stdout", output):
            tool.emit_tui_summary(summary)
        self.assertEqual(output.getvalue(), "")

    def test_tui_download_confirmation_emits_movie_and_path_for_ui_child(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"TITLE_SEARCH_TUI_CHILD": "1"}), \
             mock.patch("sys.stdout", output):
            tool.emit_tui_download("Example.mp4", "C:/Movies/Example.en.srt")
        self.assertEqual(
            output.getvalue(),
            'TITLE_SEARCH_DOWNLOAD:{"movie":"Example.mp4","subtitle_path":"C:/Movies/Example.en.srt"}\n',
        )

        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("sys.stdout", output):
            tool.emit_tui_download("Example.mp4", "C:/Movies/Example.en.srt")
        self.assertEqual(output.getvalue(), "")

    def test_get_request_retries_service_error_then_succeeds(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        response.__enter__.return_value.headers = {}
        opener = mock.Mock()
        opener.open.side_effect = [
            urllib.error.HTTPError("url", 503, "busy", {}, io.BytesIO()),
            response,
        ]
        with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener) as build_opener, \
             mock.patch.object(tool.time, "sleep") as sleep:
            body, _ = tool.request("https://example.invalid")
        self.assertEqual(body, b"ok")
        sleep.assert_called_once_with(1)
        self.assertEqual(opener.open.call_count, 2)
        build_opener.assert_called_with(tool.CredentialRedirectHandler)

    def test_post_request_is_not_retried(self):
        failure = urllib.error.HTTPError("url", 503, "busy", {}, io.BytesIO())
        opener = mock.Mock()
        opener.open.side_effect = failure
        with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(tool.ApiFailure):
                tool.request("https://example.invalid", method="POST", data=b"{}")
        self.assertEqual(opener.open.call_count, 1)

    def test_retries_timeouts_only_three_times(self):
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("late")
        with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener), \
             mock.patch.object(tool.time, "sleep") as sleep:
            with self.assertRaises(tool.ApiFailure) as raised:
                tool.request("https://example.invalid")
        self.assertTrue(raised.exception.outage)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_authenticated_redirects_are_limited_to_the_same_origin(self):
        handler = tool.CredentialRedirectHandler()
        request = tool.urllib.request.Request(
            "https://api.opensubtitles.com/api/v1/subtitles",
            headers={"Api-Key": "test-key", "Authorization": "Bearer test-token"},
        )
        with self.assertRaisesRegex(urllib.error.HTTPError, "cross-origin redirect"):
            handler.redirect_request(
                request, io.BytesIO(), 302, "Found", {},
                "https://attacker.example/collect",
            )

        same_origin = handler.redirect_request(
            request, io.BytesIO(), 302, "Found", {},
            "https://api.opensubtitles.com/api/v1/next",
        )
        self.assertEqual(same_origin.full_url, "https://api.opensubtitles.com/api/v1/next")
        self.assertEqual(same_origin.get_header("Api-key"), "test-key")
        self.assertEqual(same_origin.get_header("Authorization"), "Bearer test-token")

    def test_unauthenticated_download_redirects_remain_supported(self):
        handler = tool.CredentialRedirectHandler()
        request = tool.urllib.request.Request(
            "https://api.opensubtitles.com/download", headers={"User-Agent": "test"}
        )
        redirected = handler.redirect_request(
            request, io.BytesIO(), 302, "Found", {},
            "https://downloads.example/subtitle.zip",
        )
        self.assertEqual(redirected.full_url, "https://downloads.example/subtitle.zip")

    def test_login_accepts_documented_api_hosts(self):
        for host in ("api.opensubtitles.com", "vip-api.opensubtitles.com"):
            with self.subTest(host=host):
                session = {"base_url": host, "token": "test-token"}
                with mock.patch.dict(os.environ, {
                    "OPENSUBTITLES_USERNAME": "test-user",
                    "OPENSUBTITLES_PASSWORD": "test-password",
                }, clear=True), \
                     mock.patch.object(tool, "load_credentials", return_value={}), \
                     mock.patch.object(tool, "request", return_value=(json.dumps(session).encode(), {})):
                    token, api_base, _ = tool.login("test-key")
                self.assertEqual(token, "test-token")
                self.assertEqual(api_base, f"https://{host}/api/v1")

    def test_login_rejects_unsupported_api_hosts(self):
        invalid_hosts = (
            "https://attacker.example",
            "https://api.opensubtitles.com.attacker.example",
            "https://user@api.opensubtitles.com",
            "https://api.opensubtitles.com:444",
            "https://api.opensubtitles.com:invalid",
            "http://api.opensubtitles.com",
        )
        for host in invalid_hosts:
            with self.subTest(host=host):
                session = {"base_url": host, "token": "test-token"}
                with mock.patch.dict(os.environ, {
                    "OPENSUBTITLES_USERNAME": "test-user",
                    "OPENSUBTITLES_PASSWORD": "test-password",
                }, clear=True), \
                     mock.patch.object(tool, "load_credentials", return_value={}), \
                     mock.patch.object(tool, "request", return_value=(json.dumps(session).encode(), {})):
                    with self.assertRaisesRegex(ValueError, "unsupported API host"):
                        tool.login("test-key")

    def test_runtime_requirements_are_exact_and_hash_pinned(self):
        requirements_file = MODULE_PATH.parent / "requirements.txt"
        lines = requirements_file.read_text(encoding="utf-8").splitlines()
        self.assertIn("--require-hashes", lines)
        entries = [index for index, line in enumerate(lines) if "==" in line]
        self.assertEqual(len(entries), 9)
        for index in entries:
            with self.subTest(requirement=lines[index]):
                self.assertTrue(lines[index].endswith("\\"))
                hash_line = lines[index + 1].strip()
                self.assertTrue(hash_line.startswith("--hash=sha256:"))
                digest = hash_line.removeprefix("--hash=sha256:")
                self.assertEqual(len(digest), 64)
                self.assertTrue(all(character in "0123456789abcdef" for character in digest))

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

    def test_dependency_preflight_requires_supported_python_only(self):
        with mock.patch.object(tool.sys, "version_info", (3, 9)):
            problems = tool.preflight()
        self.assertEqual(problems, ["Python 3.10 or newer is required."])

    def test_dependency_preflight_accepts_supported_python_without_whisper_or_ffmpeg(self):
        with mock.patch.object(tool.sys, "version_info", (3, 10)), \
             mock.patch.object(tool.shutil, "which", return_value=None):
            self.assertEqual(tool.preflight(), [])

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
