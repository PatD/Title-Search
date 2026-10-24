"""Tests for Title Search's Textual interface actions and candidate labels."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from textual.widgets import Input

from title_search_tui import (
    AsciiGradient,
    CandidateScreen,
    ScanIndicator,
    TitleSearchApp,
    format_quota_status,
    format_run_summary,
    normalize_folder_path,
)


class TitleSearchTuiTests(unittest.TestCase):
    def test_app_exposes_title_palette_and_keyboard_actions(self):
        app = TitleSearchApp()

        self.assertEqual(app.title, "Title Search: Read What You Hear")
        self.assertIn(("ctrl+q", "quit_app", "Quit"), app.BINDINGS)
        self.assertIn(("ctrl+r", "start_scan", "Scan library"), app.BINDINGS)
        self.assertIn("#123824", app.CSS)
        self.assertIn("#f3c76a", app.CSS)
        launcher = Path(__file__).resolve().parents[1] / "Start Title Search.bat"
        launcher_text = launcher.read_text(encoding="utf-8")
        self.assertIn('set "PYTHONUTF8=1"', launcher_text)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', launcher_text)
        self.assertIn("-m pip install --require-hashes -r", launcher_text)

    def test_command_palette_hides_screenshot_and_theme_commands(self):
        app = TitleSearchApp()
        screen = SimpleNamespace(
            query=lambda selector: False,
            maximized=None,
            focused=None,
        )

        titles = {command.title for command in app.get_system_commands(screen)}

        self.assertNotIn("Screenshot", titles)
        self.assertNotIn("Theme", titles)
        self.assertIn("Quit", titles)
        self.assertIn("Set download cap", titles)

    def test_ascii_gradient_uses_a_background_layer(self):
        app = TitleSearchApp()
        backdrop = next(app.compose())
        gradient = AsciiGradient.gradient_text(8, 3)

        self.assertIsInstance(backdrop, AsciiGradient)
        self.assertEqual(backdrop.id, "backdrop")
        self.assertEqual(len(gradient.splitlines()), 3)
        self.assertEqual(len(gradient.splitlines()[0]), 8)
        self.assertNotEqual(gradient.splitlines()[0], gradient.splitlines()[-1])
        self.assertIn("Screen { layers: backdrop default;", app.CSS)
        self.assertIn("#backdrop { layer: backdrop;", app.CSS)

    @unittest.skipUnless(os.name == "nt", "Windows accepts both slash directions in paths")
    def test_folder_input_accepts_both_windows_separator_styles(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder_path:
            forward_slashes = folder_path.replace("\\", "/")
            backward_slashes = folder_path.replace("/", "\\")

            self.assertEqual(
                normalize_folder_path(forward_slashes),
                normalize_folder_path(backward_slashes),
            )
            self.assertTrue(normalize_folder_path(forward_slashes).is_dir())
            self.assertTrue(normalize_folder_path(backward_slashes).is_dir())

    def test_scan_and_credentials_events_are_dispatched_as_workers(self):
        for button_id, expected_group in (("start", "scan"), ("credentials", "credentials")):
            with self.subTest(button=button_id):
                app = TitleSearchApp()
                app.run_worker = mock.Mock()
                app._require_folder_path = mock.Mock(return_value="C:/Movies")
                event = mock.Mock()
                event.button.id = button_id

                app.on_button_pressed(event)

                app.run_worker.assert_called_once()
                self.assertEqual(app.run_worker.call_args.kwargs["group"], expected_group)
                app.run_worker.call_args.args[0].close()

    def test_launch_without_folder_reports_error_and_does_not_start_worker(self):
        app = TitleSearchApp()
        folder = mock.Mock(spec=Input)
        folder.value = "  "
        log = mock.Mock()
        app.query_one = mock.Mock(side_effect=lambda selector, *_: {
            "#folder": folder,
            "#run-log": log,
        }[selector])
        app.set_status = mock.Mock()
        app.notify = mock.Mock()
        app.run_worker = mock.Mock()

        event = mock.Mock()
        event.button.id = "start"
        app.on_button_pressed(event)

        app.set_status.assert_called_once_with("Enter a library folder path first.")
        log.write.assert_called_once_with("Enter a library folder path first.")
        app.notify.assert_called_once_with(
            "Enter a library folder path first.",
            title="Folder required",
            severity="warning",
        )
        folder.focus.assert_called_once_with()
        app.run_worker.assert_not_called()

    def test_download_summary_preserves_other_scan_results(self):
        message = format_run_summary({
            "videos_found": 4,
            "ignored_existing": 1,
            "outputs_created": 2,
            "downloaded_subtitles": 1,
            "without_subtitles": 1,
            "failures": 1,
            "warnings": 0,
        })

        self.assertIn("Downloaded 1 subtitle file(s) successfully", message)
        self.assertIn("2 subtitle file(s) created total", message)
        self.assertIn("1 existing SRT(s) skipped", message)
        self.assertIn("1 video(s) still without subtitles", message)
        self.assertIn("1 issue(s)", message)

    def test_quota_status_estimates_download_batches_and_separates_scans(self):
        message = format_quota_status({
            "remaining_downloads": 23,
            "daily_limit": 30,
            "per_run_cap": 10,
            "account": "VIP",
        })

        self.assertIn("23 of 30 daily subtitle downloads remain", message)
        self.assertIn("About 2 full run(s)", message)
        self.assertIn("plus 3 download(s)", message)
        self.assertIn("Scans themselves are not quota-limited", message)

    def test_quota_status_reports_exhaustion_and_unavailable_account(self):
        exhausted = format_quota_status({
            "remaining_downloads": 0,
            "daily_limit": 10,
            "per_run_cap": 10,
            "account": "Free",
        })
        unavailable = format_quota_status({
            "remaining_downloads": None,
            "account": "API unavailable",
        })

        self.assertIn("0 of 10 daily subtitle downloads remain", exhausted)
        self.assertIn("scans still work", exhausted)
        self.assertIn("couldn't be verified", unavailable)
        self.assertIn("API unavailable", unavailable)

    def test_download_confirmation_updates_status_and_logs_path(self):
        class AsyncLines:
            def __init__(self, lines):
                self.lines = iter(lines)

            async def readline(self):
                return next(self.lines, b"")

        app = TitleSearchApp()
        path = "C:/Movies/Example/Example.en.srt"
        app.process = SimpleNamespace(
            stdout=AsyncLines([f"Downloaded successfully: {path}\n".encode()]),
            stdin=mock.Mock(),
        )
        app.set_status = mock.Mock()
        app.write_log = mock.Mock()

        with self.assertRaises(StopIteration):
            app.consume_process_output().send(None)

        app.set_status.assert_called_once_with("Subtitle downloaded successfully.")
        app.write_log.assert_called_once_with(f"Downloaded successfully: {path}")

    def test_download_confirmation_notifies_with_movie_name_and_saved_path(self):
        class AsyncLines:
            def __init__(self, lines):
                self.lines = iter(lines)

            async def readline(self):
                return next(self.lines, b"")

        movie = "The Example Movie.mp4"
        path = "C:/Movies/The Example Movie/The Example Movie.en.srt"
        marker = "TITLE_SEARCH_DOWNLOAD:" + json.dumps({
            "movie": movie,
            "subtitle_path": path,
        })
        app = TitleSearchApp()
        app.process = SimpleNamespace(
            stdout=AsyncLines([f"{marker}\n".encode()]),
            stdin=mock.Mock(),
        )
        app.set_status = mock.Mock()
        app.notify = mock.Mock()
        app.write_log = mock.Mock()

        with self.assertRaises(StopIteration):
            app.consume_process_output().send(None)

        app.set_status.assert_called_once_with(
            "Subtitle downloaded successfully for The Example Movie.mp4."
        )
        app.notify.assert_called_once_with(
            f"{movie}\n{path}",
            title="Subtitle downloaded successfully",
            severity="information",
            timeout=12,
        )


    def test_scan_indicator_animates_and_can_be_hidden(self):
        indicator = ScanIndicator()
        indicator.update = mock.Mock()

        indicator.start_scanning()
        first_frame = indicator.update.call_args.args[0]
        indicator._advance()
        next_frame = indicator.update.call_args.args[0]

        self.assertTrue(indicator.display)
        self.assertNotEqual(str(first_frame), str(next_frame))
        indicator.stop_scanning()
        self.assertFalse(indicator.display)

    def test_scan_ui_locks_configuration_and_leaves_cancel_enabled(self):
        app = TitleSearchApp()
        selectors = (
            "#folder", "#style-select", "#recursive",
            "#start", "#credentials", "#clear", "#cancel",
        )
        widgets = {selector: mock.Mock(disabled=False) for selector in selectors}
        indicator = mock.Mock(spec=ScanIndicator)
        widgets["#scan-indicator"] = indicator
        app.query_one = mock.Mock(side_effect=lambda selector, *_: widgets[selector])

        app.set_scanning_ui(True)

        self.assertTrue(all(widgets[selector].disabled for selector in selectors[:-1]))
        self.assertFalse(widgets["#cancel"].disabled)
        indicator.start_scanning.assert_called_once_with()

        app.set_scanning_ui(False)

        self.assertTrue(all(not widgets[selector].disabled for selector in selectors[:-1]))
        self.assertTrue(widgets["#cancel"].disabled)
        indicator.stop_scanning.assert_called_once_with()

    def test_cancel_scan_terminates_process_and_reports_status(self):
        app = TitleSearchApp()
        process = SimpleNamespace(returncode=None, terminate=mock.Mock())
        app.process = process
        app._scan_active = True
        app.set_status = mock.Mock()
        app.write_log = mock.Mock()

        app.cancel_scan()

        self.assertTrue(app._cancel_requested)
        process.terminate.assert_called_once_with()
        app.set_status.assert_called_once_with("Stopping scan…")
        app.write_log.assert_called_once_with("Cancel requested.")

    def test_subprocess_startup_failure_is_reported_and_controls_reenabled(self):
        async def fail_launch(*args, **kwargs):
            raise subprocess.SubprocessError("launch failed")

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder_path:
            app = TitleSearchApp()
            folder = mock.Mock(spec=Input, value=folder_path)
            style = mock.Mock(value="standard")
            recursive = mock.Mock(value=True)
            start = mock.Mock(disabled=False)
            cancel = mock.Mock(disabled=True)
            log = mock.Mock()
            indicator = mock.Mock(spec=ScanIndicator)
            widgets = {
                "#folder": folder,
                "#style-select": style,
                "#recursive": recursive,
                "#start": start,
                "#credentials": mock.Mock(disabled=False),
                "#clear": mock.Mock(disabled=False),
                "#cancel": cancel,
                "#scan-indicator": indicator,
                "#run-log": log,
                "#quota-status": mock.Mock(),
            }
            app.query_one = mock.Mock(side_effect=lambda selector, *_: widgets[selector])
            app.set_status = mock.Mock()
            app._credentials = {"api_key": "test-key", "username": "", "password": ""}
            app._max_downloads = 5

            with mock.patch(
                "title_search_tui.asyncio.create_subprocess_exec",
                new=fail_launch,
            ):
                with self.assertRaises(StopIteration):
                    app.start_scan().send(None)

        app.set_status.assert_any_call("Could not start the subtitle engine.")
        self.assertTrue(any(
            call.args == ("Startup error: launch failed",)
            for call in log.write.call_args_list
        ))
        self.assertFalse(start.disabled)
        self.assertTrue(cancel.disabled)

    def test_repeated_scan_after_unexpected_error_restores_ui(self):
        class AsyncLines:
            def __init__(self, lines):
                self.lines = iter(lines)

            async def readline(self):
                return next(self.lines, b"")

        class FakeProcess:
            def __init__(self):
                summary = {
                    "videos_found": 1,
                    "ignored_existing": 0,
                    "outputs_created": 1,
                    "downloaded_subtitles": 1,
                    "without_subtitles": 0,
                    "failures": 0,
                    "warnings": 0,
                }
                self.stdout = AsyncLines([
                    ("TITLE_SEARCH_SUMMARY:" + json.dumps(summary) + "\n").encode()
                ])
                self.stdin = mock.Mock()
                self.returncode = None

            async def wait(self):
                self.returncode = 0
                return self.returncode

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder_path:
            app = TitleSearchApp()
            folder = mock.Mock(spec=Input, value=folder_path)
            start = mock.Mock(disabled=False)
            cancel = mock.Mock(disabled=True)
            indicator = mock.Mock(spec=ScanIndicator)
            widgets = {
                "#folder": folder,
                "#style-select": mock.Mock(value="standard"),
                "#recursive": mock.Mock(value=True),
                "#start": start,
                "#credentials": mock.Mock(disabled=False),
                "#clear": mock.Mock(disabled=False),
                "#cancel": cancel,
                "#scan-indicator": indicator,
                "#run-log": mock.Mock(),
                "#quota-status": mock.Mock(),
            }
            app.query_one = mock.Mock(side_effect=lambda selector, *_: widgets[selector])
            app.set_status = mock.Mock()
            app.write_log = mock.Mock()
            app._credentials = {"api_key": "test-key", "username": "", "password": ""}
            app._max_downloads = 5
            launches = 0
            child_environments = []

            async def create_process(*args, **kwargs):
                nonlocal launches
                launches += 1
                child_environments.append(kwargs["env"])
                if launches == 1:
                    raise RuntimeError("temporary worker failure")
                return FakeProcess()

            with mock.patch("title_search_tui.asyncio.create_subprocess_exec", new=create_process):
                with self.assertRaises(StopIteration):
                    app.start_scan().send(None)

                self.assertFalse(app._scan_active)
                self.assertFalse(start.disabled)
                self.assertTrue(cancel.disabled)
                self.assertTrue(any(
                    call.args == ("Unexpected scan error: RuntimeError: temporary worker failure",)
                    for call in app.write_log.call_args_list
                ))

                with self.assertRaises(StopIteration):
                    app.start_scan().send(None)

        self.assertEqual(launches, 2)
        self.assertEqual(len(child_environments), 2)
        self.assertTrue(all(env["PYTHONUTF8"] == "1" for env in child_environments))
        self.assertTrue(all(env["PYTHONIOENCODING"] == "utf-8" for env in child_environments))
        self.assertFalse(app._scan_active)
        self.assertFalse(start.disabled)
        self.assertTrue(cancel.disabled)
        self.assertEqual(indicator.start_scanning.call_count, 2)
        self.assertEqual(indicator.stop_scanning.call_count, 2)

    def test_candidate_label_includes_release_fps_language_and_match_style(self):
        label = CandidateScreen.format_candidate(0, {
            "release": "Example.Release",
            "fps": "23.976 matches video",
            "language": "en",
            "type": "Standard",
            "downloads": "42",
        })
        rendered = str(label)

        self.assertIn("Example.Release", rendered)
        self.assertIn("23.976 matches video", rendered)
        self.assertIn("en English", rendered)
        self.assertIn("42 downloads", rendered)
        self.assertTrue(any("7fc69a" in str(span.style) for span in label.spans))

    def test_candidate_dialog_title_includes_source_fps(self):
        self.assertEqual(
            CandidateScreen.format_title("Example.mkv", 23.976),
            "Choose a subtitle for Example.mkv · 23.976 FPS",
        )

    def test_candidate_dialog_title_shows_unknown_source_fps(self):
        self.assertEqual(
            CandidateScreen.format_title("Example.mkv", None),
            "Choose a subtitle for Example.mkv · FPS unknown",
        )

    def test_candidate_mismatch_is_visually_flagged(self):
        label = CandidateScreen.format_candidate(1, {
            "release": "Wrong.Frame.Rate",
            "fps": "25.000 FPS MISMATCH",
        })

        self.assertIn("MISMATCH", str(label))
        self.assertTrue(any("e6a94f" in str(span.style) for span in label.spans))

    def test_candidate_selection_returns_one_based_result_number(self):
        screen = CandidateScreen({"items": []})
        screen.dismiss = mock.Mock()

        screen.on_option_list_option_selected(mock.Mock(option_index=1))

        screen.dismiss.assert_called_once_with(2)

    def test_candidate_skip_returns_no_selection(self):
        screen = CandidateScreen({"items": []})
        screen.dismiss = mock.Mock()

        screen.action_skip()

        screen.dismiss.assert_called_once_with(None)

    def test_candidate_picker_keeps_cancel_and_quit_available(self):
        self.assertIn(("ctrl+c", "cancel_scan", "Cancel scan"), CandidateScreen.BINDINGS)
        self.assertIn(("ctrl+q", "quit_app", "Quit app"), CandidateScreen.BINDINGS)

    def test_run_summary_calls_out_when_every_movie_already_has_srt(self):
        message = format_run_summary({
            "videos_found": 4,
            "ignored_existing": 4,
            "outputs_created": 0,
            "without_subtitles": 0,
            "failures": 0,
            "warnings": 0,
        })

        self.assertEqual(
            message,
            "Found 4 video(s); all already had matching .srt subtitle files.",
        )

    def test_run_summary_reports_empty_and_partial_scans(self):
        self.assertEqual(
            format_run_summary({"videos_found": 0}),
            "No video files were found in the selected folder.",
        )
        message = format_run_summary({
            "videos_found": 5,
            "ignored_existing": 2,
            "outputs_created": 2,
            "without_subtitles": 1,
            "failures": 1,
            "warnings": 1,
        })
        self.assertIn("5 video(s)", message)
        self.assertIn("2 already had .srt files", message)
        self.assertIn("2 subtitle file(s) created", message)
        self.assertIn("2 issue(s)", message)


if __name__ == "__main__":
    unittest.main()
