"""Tests for Title Search's Textual interface actions and candidate labels."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textual.widgets import Input

from title_search_tui import (
    CandidateScreen,
    TitleSearchApp,
    format_run_summary,
    normalize_folder_path,
)


class TitleSearchTuiTests(unittest.TestCase):
    def test_app_exposes_title_palette_and_keyboard_actions(self):
        app = TitleSearchApp()

        self.assertEqual(app.title, "Title Search")
        self.assertIn(("ctrl+q", "quit_app", "Quit"), app.BINDINGS)
        self.assertIn(("ctrl+r", "start_scan", "Scan library"), app.BINDINGS)
        self.assertIn("#123824", app.CSS)
        self.assertIn("#f3c76a", app.CSS)

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
                event = mock.Mock()
                event.button.id = button_id

                app.on_button_pressed(event)

                app.run_worker.assert_called_once()
                self.assertEqual(app.run_worker.call_args.kwargs["group"], expected_group)
                app.run_worker.call_args.args[0].close()

    def test_start_without_folder_sets_inline_guidance(self):
        app = TitleSearchApp()
        folder = mock.Mock(spec=Input)
        folder.value = "  "
        app.query_one = mock.Mock(return_value=folder)
        app.set_status = mock.Mock()

        with self.assertRaises(StopIteration):
            app.start_scan().send(None)

        app.set_status.assert_called_once_with("Enter a library folder path first.")
        folder.focus.assert_called_once_with()

    def test_subprocess_startup_failure_is_reported_and_controls_reenabled(self):
        async def fail_launch(*args, **kwargs):
            raise subprocess.SubprocessError("launch failed")

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder_path:
            app = TitleSearchApp()
            folder = mock.Mock(spec=Input, value=folder_path)
            max_downloads = mock.Mock(spec=Input, value="5")
            style = mock.Mock(value="standard")
            recursive = mock.Mock(value=True)
            transcribe = mock.Mock(value=False)
            start = mock.Mock(disabled=False)
            cancel = mock.Mock(disabled=True)
            log = mock.Mock()
            widgets = {
                "#folder": folder,
                "#max-downloads": max_downloads,
                "#style-select": style,
                "#recursive": recursive,
                "#transcribe": transcribe,
                "#start": start,
                "#cancel": cancel,
                "#run-log": log,
            }
            app.query_one = mock.Mock(side_effect=lambda selector, *_: widgets[selector])
            app.set_status = mock.Mock()
            app._credentials = {"api_key": "test-key", "username": "", "password": ""}

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
            "All 4 video(s) already have matching .srt files. Nothing to do.",
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
