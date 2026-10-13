"""Full-screen, keyboard-driven interface for Title Search."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
)
from textual.widgets.option_list import Option
from rich.text import Text

from subtitle_tool import FREE_ACCOUNT_DAILY_CAP, load_credentials


MARKER = "TITLE_SEARCH_CANDIDATES:"
SUMMARY_MARKER = "TITLE_SEARCH_SUMMARY:"
QUOTA_MARKER = "TITLE_SEARCH_QUOTA:"
DOWNLOAD_MARKER = "TITLE_SEARCH_DOWNLOAD:"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CLI_SCRIPT = Path(__file__).with_name("subtitle_tool.py")


class AsciiGradient(Static):
    """A subtle, resize-aware ASCII texture rendered behind the interface."""

    RAMP = " .,:-=+*#"

    def __init__(self, **kwargs):
        super().__init__("", markup=False, **kwargs)
        self._last_size = (0, 0)

    def on_mount(self) -> None:
        self.refresh_gradient()

    def on_resize(self, event: Resize) -> None:
        self.refresh_gradient()

    @classmethod
    def gradient_text(cls, width: int, height: int) -> str:
        if width < 1 or height < 1:
            return ""
        ramp_max = len(cls.RAMP) - 1
        rows = []
        for y in range(height):
            vertical = y / max(1, height - 1)
            row = []
            for x in range(width):
                horizontal = x / max(1, width - 1)
                level = round((horizontal * 0.72 + vertical * 0.28) * ramp_max)
                row.append(cls.RAMP[level])
            rows.append("".join(row))
        return "\n".join(rows)

    def refresh_gradient(self) -> None:
        width, height = self.size.width, self.size.height
        if (width, height) == self._last_size or width < 1 or height < 1:
            return
        self._last_size = (width, height)
        self.update(self.gradient_text(width, height))


def normalize_folder_path(value: str) -> Path:
    """Normalize a path typed or pasted with either Windows separator style."""
    cleaned = value.strip().strip('"')
    return Path(os.path.normpath(cleaned)).expanduser()


def format_run_summary(summary: dict) -> str:
    videos = int(summary.get("videos_found", 0))
    ignored = int(summary.get("ignored_existing", 0))
    if videos == 0:
        return "No video files were found in the selected folder."
    if ignored == videos:
        return f"Found {videos} video(s); all already had matching .srt subtitle files."
    downloaded = int(summary.get("downloaded_subtitles", 0))
    outputs = int(summary.get("outputs_created", 0))
    without_subtitles = int(summary.get("without_subtitles", 0))
    issues = int(summary.get("failures", 0)) + int(summary.get("warnings", 0))
    if downloaded:
        return (
            f"Downloaded {downloaded} subtitle file(s) successfully; "
            f"{outputs} subtitle file(s) created total; {ignored} existing SRT(s) skipped; "
            f"{without_subtitles} video(s) still without subtitles; {issues} issue(s)."
        )
    return (
        f"Scan complete: {videos} video(s); {ignored} already had .srt files; "
        f"{outputs} subtitle file(s) created; {without_subtitles} without subtitles; "
        f"{issues} issue(s)."
    )


def format_quota_status(quota: dict) -> str:
    """Describe account allowance without implying scans consume downloads."""
    remaining = quota.get("remaining_downloads")
    limit = quota.get("daily_limit")
    account = quota.get("account", "OpenSubtitles account")
    if remaining is None:
        return f"OpenSubtitles allowance couldn't be verified ({account}). Scanning does not use download quota."
    remaining = max(0, int(remaining))
    if limit is not None:
        allowance = f"{remaining} of {int(limit)} daily subtitle downloads remain"
    else:
        allowance = f"{remaining} subtitle download(s) remain"
    if remaining == 0:
        return f"OpenSubtitles: {allowance}. Downloads may resume when the allowance resets; scans still work."
    per_run = max(1, int(quota.get("per_run_cap", 1)))
    runs, partial = divmod(remaining, per_run)
    capacity = f"About {runs} full run(s) at the current {per_run}-download cap"
    if partial:
        capacity += f", plus {partial} download(s)"
    return f"OpenSubtitles: {allowance}. {capacity}. Scans themselves are not quota-limited."


class CredentialScreen(ModalScreen[dict | None]):
    """Collect optional API/account values without saving them to disk."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    CredentialScreen { align: center middle; background: $background 80%; }
    #credential-dialog { width: 76; height: auto; max-height: 90%; padding: 1 2; border: round #d5a443; background: #10271c; }
    #credential-title { color: #f3c76a; text-style: bold; margin-bottom: 1; }
    #credential-note { color: #b9c9bd; height: auto; margin-bottom: 1; }
    #credential-dialog Input { margin-bottom: 1; border: round #496c54; }
    #credential-error { color: #ffbf69; height: 1; margin-bottom: 1; }
    #credential-buttons { height: 3; align-horizontal: right; }
    #credential-buttons Button { margin-left: 1; }
    """

    def __init__(self, values: dict, **kwargs):
        super().__init__(**kwargs)
        self.values = values

    def compose(self) -> ComposeResult:
        with Vertical(id="credential-dialog"):
            yield Static("OpenSubtitles access", id="credential-title")
            yield Static(
                "An API key enables subtitle search. An account username and password "
                "are needed to download. Values are used for this run and are not saved here.",
                id="credential-note",
            )
            yield Label("API key")
            yield Input(value=self.values.get("api_key", ""), password=True, id="api-key")
            yield Label("Username")
            yield Input(value=self.values.get("username", ""), id="api-username")
            yield Label("Password")
            yield Input(value=self.values.get("password", ""), password=True, id="api-password")
            yield Static("", id="credential-error")
            with Horizontal(id="credential-buttons"):
                yield Button("Cancel", id="credential-cancel")
                yield Button("Search only", id="credential-search")
                yield Button("Continue", variant="primary", id="credential-continue")

    def on_mount(self) -> None:
        self.query_one("#api-key", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "credential-cancel":
            self.dismiss(None)
            return
        if event.button.id not in {"credential-continue", "credential-search"}:
            return
        values = {
            "api_key": self.query_one("#api-key", Input).value.strip(),
            "username": self.query_one("#api-username", Input).value.strip(),
            "password": self.query_one("#api-password", Input).value,
            "mode": "search" if event.button.id == "credential-search" else "normal",
        }
        if values["mode"] == "normal" and values["username"] and not values["password"]:
            self.query_one("#credential-error", Static).update(
                "Enter the account password, or choose Search only."
            )
            return
        self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CandidateScreen(ModalScreen[int | None]):
    """Keyboard picker for subtitle releases returned by the CLI engine."""

    BINDINGS = [
        ("escape", "skip", "Skip movie"),
        ("ctrl+c", "cancel_scan", "Cancel scan"),
        ("ctrl+q", "quit_app", "Quit app"),
    ]

    CSS = """
    CandidateScreen { align: center middle; background: $background 80%; }
    #candidate-dialog { width: 90%; max-width: 112; height: 80%; max-height: 34; padding: 1 2; border: round #d5a443; background: #10271c; }
    #candidate-title { color: #f3c76a; text-style: bold; height: auto; margin-bottom: 1; }
    #candidate-warning { color: #ffbf69; height: auto; margin-bottom: 1; }
    #candidate-list { height: 1fr; border: round #496c54; }
    #candidate-help { color: #b9c9bd; height: 2; margin-top: 1; }
    """

    def __init__(self, payload: dict, **kwargs):
        super().__init__(**kwargs)
        self.payload = payload

    @staticmethod
    def format_candidate(index: int, candidate: dict) -> Text:
        fps = candidate.get("fps", "FPS unknown")
        release = candidate.get("release", "Release unknown")
        language = candidate.get("language", "en")
        kind = candidate.get("type", "Standard")
        downloads = candidate.get("downloads", "0")
        row = Text()
        row.append(f"{index + 1:>2}. {release}\n", style="bold #f1ead8")
        fps_style = "bold #e6a94f" if "MISMATCH" in fps else (
            "bold #7fc69a" if "matches video" in fps else "#d5bd82"
        )
        row.append(f"    {fps}", style=fps_style)
        row.append(f"  ·  {language} English  ·  {kind}  ·  {downloads} downloads", style="#b9c9bd")
        return row

    def compose(self) -> ComposeResult:
        movie = self.payload.get("movie", "Movie")
        title_search = self.payload.get("title_search", False)
        candidates = self.payload.get("items", [])
        with Vertical(id="candidate-dialog"):
            yield Static(f"Choose a subtitle for {movie}", id="candidate-title")
            yield Static(
                "Title search results are not exact file matches. Confirm the title, year, and cut."
                if title_search else
                "Hash results match the video file hash. Compare release FPS with the movie FPS.",
                id="candidate-warning",
            )
            options = []
            for index, candidate in enumerate(candidates):
                options.append(Option(self.format_candidate(index, candidate)))
            yield OptionList(*options, id="candidate-list", markup=False)
            yield Static("↑ / ↓ move   Enter choose   Esc skip\nCtrl+C cancel scan   Ctrl+Q quit", id="candidate-help")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index + 1)

    def action_skip(self) -> None:
        self.dismiss(None)

    def action_cancel_scan(self) -> None:
        self.app.cancel_scan()
        self.dismiss(None)

    async def action_quit_app(self) -> None:
        self.dismiss(None)
        await self.app.action_quit_app()


class ScanIndicator(Static):
    """Animated, non-numeric scan activity display."""

    MESSAGES = (
        "THE REELS ARE TURNING",
        "CHECKING TITLES AND YEARS",
        "MATCHING FRAME RATES",
        "SEARCHING SUBTITLE RELEASES",
        "CATALOGING YOUR LIBRARY",
    )
    TRACK_LENGTH = 18

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._frame = 0

    def on_mount(self) -> None:
        self.set_interval(0.18, self._advance)

    def start_scanning(self) -> None:
        self._frame = 0
        self.display = True
        self._render_frame()

    def stop_scanning(self) -> None:
        self.display = False

    def _advance(self) -> None:
        if self.display:
            self._frame += 1
            self._render_frame()

    def _render_frame(self) -> None:
        text = Text()
        text.append("  ✦  ", style="bold #f3c76a")
        text.append(self.MESSAGES[(self._frame // 9) % len(self.MESSAGES)], style="bold #f3c76a")
        text.append("  ✦\n  [", style="#b9c9bd")
        travel = self.TRACK_LENGTH * 2 - 2
        position = self._frame % travel
        if position >= self.TRACK_LENGTH:
            position = travel - position
        text.append("━" * position, style="#496c54")
        text.append("◆", style="bold #e7bd61")
        text.append("━" * (self.TRACK_LENGTH - position - 1), style="#496c54")
        text.append("]  Working through the collection…", style="#b9c9bd")
        self.update(text)


class TitleSearchApp(App[None]):
    """Title Search full-screen TUI."""

    TITLE = "Title Search: Read What You Hear"
    SUB_TITLE = ""
    BINDINGS = [
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+r", "start_scan", "Scan library"),
    ]

    def get_system_commands(self, screen) -> Iterator[SystemCommand]:
        """Keep the useful palette commands but hide screenshot and theme actions."""
        hidden_commands = {"screenshot", "theme"}
        yield from (
            command
            for command in super().get_system_commands(screen)
            if command.title.casefold() not in hidden_commands
        )

    CSS = """
    Screen { layers: backdrop default; background: #111915; color: #e5eee7; }
    #backdrop { layer: backdrop; position: absolute; width: 100%; height: 100%; color: #27392f; background: transparent; }
    Header { background: #123824; color: #f3c76a; }
    Footer { background: #0c2116; color: #e7bd61; }
    #shell { height: 1fr; padding: 1 2; }
    .section-title { color: #e7bd61; text-style: bold; margin-top: 1; }
    #folder { margin-top: 1; border: round #496c54; }
    #options { height: 5; margin-top: 1; }
    #style-select { width: 1fr; margin-right: 1; }
    #max-downloads { width: 22; }
    #toggles { height: 3; margin-top: 1; }
    #toggles Checkbox { margin-right: 3; }
    #actions { height: 3; margin-top: 1; }
    #actions Button { margin-right: 1; }
    #run-status { height: 1; color: #e7bd61; margin-top: 1; }
    #quota-status { height: 2; color: #b9c9bd; border: round #315b42; background: #091810; padding: 0 1; margin-top: 1; }
    #scan-indicator { display: none; height: 4; border: round #d5a443; background: #10271c; padding: 0 1; margin-top: 1; }
    #run-log { height: 1fr; min-height: 7; border: round #315b42; background: #091810; margin-top: 1; padding: 0 1; scrollbar-color: #547d5d; }
    Button { background: #183b27; color: #e5eee7; border: round #496c54; }
    Button.-primary { background: #c3943e; color: #10180f; text-style: bold; }
    Input:focus, Select:focus, OptionList:focus { border: round #e7bd61; }
    """

    def __init__(self, initial_folder: str = "", **kwargs):
        super().__init__(**kwargs)
        self.initial_folder = initial_folder
        self.process: asyncio.subprocess.Process | None = None
        self._run_summary: dict | None = None
        self._credentials: dict | None = None
        self._credential_mode = "normal"
        self._scan_active = False
        self._cancel_requested = False
        self._quota_info: dict | None = None

    def compose(self) -> ComposeResult:
        yield AsciiGradient(id="backdrop")
        yield Header(icon="*")
        with Vertical(id="shell"):
            yield Label("MOVIE LIBRARY", classes="section-title")
            yield Input(
                value=self.initial_folder,
                placeholder="Enter a folder path, for example C:/Movies or /Users/name/Movies",
                id="folder",
            )
            with Horizontal(id="options"):
                yield Select(
                    [("Standard English", "standard"), ("English SDH", "sdh"), ("Offer both", "both")],
                    value="standard",
                    allow_blank=False,
                    id="style-select",
                )
                yield Input(value=str(FREE_ACCOUNT_DAILY_CAP), placeholder="Max downloads", id="max-downloads")
            with Horizontal(id="toggles"):
                yield Checkbox("Search subfolders", value=True, id="recursive")
            with Horizontal(id="actions"):
                yield Button("Start scan", variant="primary", id="start")
                yield Button("Credentials", id="credentials")
                yield Button("Clear activity", id="clear")
                yield Button("Cancel scan", id="cancel", disabled=True)
            yield Static("Ready. Choose a library folder and start a scan.", id="run-status")
            yield Static("OpenSubtitles allowance: waiting for account check…", id="quota-status")
            yield ScanIndicator(id="scan-indicator")
            yield RichLog(id="run-log", markup=False, wrap=True, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#folder", Input).focus()

    def write_log(self, message: str) -> None:
        self.query_one("#run-log", RichLog).write(message.rstrip("\r\n"))

    def set_status(self, message: str) -> None:
        self.query_one("#run-status", Static).update(message)

    def update_quota_status(self, quota: dict, *, log: bool = True) -> None:
        self._quota_info = quota
        message = format_quota_status(quota)
        self.query_one("#quota-status", Static).update(message)
        if log:
            self.write_log(message)

    def show_run_summary(self, summary: dict) -> None:
        message = format_run_summary(summary)
        self.set_status(message)
        videos = int(summary.get("videos_found", 0))
        ignored = int(summary.get("ignored_existing", 0))
        if videos > 0 and ignored == videos:
            self.notify(
                message,
                title="All videos already have subtitles",
                severity="information",
                timeout=12,
            )

    def credential_values(self) -> dict:
        if self._credentials is not None:
            return self._credentials
        saved = load_credentials()
        self._credentials = {
            "api_key": os.getenv("OPENSUBTITLES_API_KEY") or saved.get("api_key", ""),
            "username": os.getenv("OPENSUBTITLES_USERNAME") or saved.get("username", ""),
            "password": os.getenv("OPENSUBTITLES_PASSWORD") or saved.get("password", ""),
        }
        return self._credentials

    async def ask_credentials(self) -> bool:
        try:
            values = self.credential_values()
        except RuntimeError as exc:
            self.set_status("Could not read credentials")
            self.write_log(str(exc))
            return False
        result = await self.push_screen_wait(CredentialScreen(values))
        if result is None:
            return False
        self._credential_mode = result.pop("mode")
        self._credentials = result
        return True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.launch_scan()
        elif event.button.id == "credentials":
            self.run_worker(
                self.edit_credentials(),
                name="credentials",
                group="credentials",
                exclusive=True,
            )
        elif event.button.id == "clear":
            self.query_one("#run-log", RichLog).clear()
            self.set_status("Activity cleared.")
        elif event.button.id == "cancel":
            self.cancel_scan()

    def launch_scan(self) -> None:
        if self._scan_active:
            return
        if not self._require_folder_path():
            return
        self.run_worker(
            self.start_scan(),
            name="scan",
            group="scan",
            exclusive=True,
        )

    def action_start_scan(self) -> None:
        self.launch_scan()

    def _require_folder_path(self) -> str | None:
        """Validate the path before scheduling a scan and make errors visible."""
        raw_path = self.query_one("#folder", Input).value.strip()
        if raw_path:
            return raw_path
        message = "Enter a library folder path first."
        self.set_status(message)
        self.write_log(message)
        self.notify(message, title="Folder required", severity="warning")
        self.query_one("#folder", Input).focus()
        return None

    async def edit_credentials(self) -> None:
        if await self.ask_credentials():
            self.set_status("Credential choices saved for this run.")

    async def action_quit_app(self) -> None:
        self._cancel_requested = self._scan_active
        if self.process and self.process.returncode is None:
            self.process.terminate()
        self.exit()

    def cancel_scan(self) -> None:
        if not self._scan_active:
            return
        self._cancel_requested = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
        self.set_status("Stopping scan…")
        self.write_log("Cancel requested.")

    def set_scanning_ui(self, scanning: bool) -> None:
        for selector in (
            "#folder",
            "#style-select",
            "#max-downloads",
            "#recursive",
            "#start",
            "#credentials",
            "#clear",
        ):
            self.query_one(selector).disabled = scanning
        self.query_one("#cancel", Button).disabled = not scanning
        indicator = self.query_one("#scan-indicator", ScanIndicator)
        if scanning:
            indicator.start_scanning()
        else:
            indicator.stop_scanning()

    async def start_scan(self) -> None:
        if self.process and self.process.returncode is None:
            self.set_status("A scan is already running.")
            return
        raw_path = self._require_folder_path()
        if raw_path is None:
            return
        folder = normalize_folder_path(raw_path)
        try:
            if not folder.is_dir():
                self.set_status(f"Folder not found: {folder}")
                return
        except OSError as exc:
            self.set_status(f"Cannot inspect folder: {exc}")
            return
        max_downloads_text = self.query_one("#max-downloads", Input).value.strip()
        try:
            max_downloads = int(max_downloads_text)
            if not 1 <= max_downloads <= FREE_ACCOUNT_DAILY_CAP:
                raise ValueError
        except ValueError:
            self.set_status(f"Max downloads must be from 1 to {FREE_ACCOUNT_DAILY_CAP}.")
            self.query_one("#max-downloads", Input).focus()
            return

        if self._credentials is None:
            try:
                saved = self.credential_values()
            except RuntimeError as exc:
                self.set_status("Credential file needs attention.")
                self.write_log(str(exc))
                return
            if not saved.get("api_key") or not (saved.get("username") and saved.get("password")):
                if not await self.ask_credentials():
                    self.set_status("Scan cancelled before credentials were provided.")
                    return

        style = self.query_one("#style-select", Select).value
        recursive = self.query_one("#recursive", Checkbox).value
        command = [sys.executable, str(CLI_SCRIPT), "--cli", str(folder),
                   "--style", str(style), "--max-downloads", str(max_downloads)]
        if not recursive:
            command.append("--no-recursive")

        env = os.environ.copy()
        env["TITLE_SEARCH_TUI_CHILD"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("TITLE_SEARCH_FORCE_SEARCH_ONLY", None)
        env.pop("TITLE_SEARCH_DISABLE_API", None)
        for variable in (
            "OPENSUBTITLES_API_KEY",
            "OPENSUBTITLES_USERNAME",
            "OPENSUBTITLES_PASSWORD",
        ):
            env.pop(variable, None)
        credentials = self._credentials or {}
        for field, variable in (
            ("api_key", "OPENSUBTITLES_API_KEY"),
            ("username", "OPENSUBTITLES_USERNAME"),
            ("password", "OPENSUBTITLES_PASSWORD"),
        ):
            if credentials.get(field):
                env[variable] = credentials[field]
        if self._credential_mode == "search":
            env["TITLE_SEARCH_FORCE_SEARCH_ONLY"] = "1"
        elif not credentials.get("api_key"):
            env["TITLE_SEARCH_DISABLE_API"] = "1"

        self.query_one("#run-log", RichLog).clear()
        self._run_summary = None
        self._quota_info = None
        self.query_one("#quota-status", Static).update("OpenSubtitles allowance: checking account…")
        self._scan_active = True
        self._cancel_requested = False
        try:
            self.set_status(f"Scanning {folder} …")
            self.set_scanning_ui(True)
            self.write_log(f"Title Search · {folder}")
            self.write_log(f"Style: {style}; recursive: {'yes' if recursive else 'no'}")
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                limit=1024 * 1024,
            )
            if self._cancel_requested and self.process.returncode is None:
                self.process.terminate()
            await self.consume_process_output()
            return_code = await self.process.wait()
            if self._cancel_requested:
                self.set_status("Scan cancelled.")
                self.write_log("Scan cancelled.")
            elif self._run_summary is not None:
                self.show_run_summary(self._run_summary)
            else:
                self.set_status("Run complete." if return_code == 0 else f"Run finished with status {return_code}.")
            self.write_log("\nDone. Press Ctrl+R to run another scan or Ctrl+Q to quit.")
        except (OSError, subprocess.SubprocessError) as exc:
            self.set_status("Could not start the subtitle engine.")
            self.write_log(f"Startup error: {exc}")
        except Exception as exc:
            self.set_status("The scan stopped after an unexpected error.")
            self.write_log(f"Unexpected scan error: {type(exc).__name__}: {exc}")
        finally:
            self.process = None
            self._scan_active = False
            self.set_scanning_ui(False)

    async def consume_process_output(self) -> None:
        assert self.process and self.process.stdout and self.process.stdin
        while raw_line := await self.process.stdout.readline():
            line = ANSI_ESCAPE.sub("", raw_line.decode("utf-8", errors="replace")).rstrip("\r\n")
            if line.startswith(SUMMARY_MARKER):
                try:
                    self._run_summary = json.loads(line[len(SUMMARY_MARKER):])
                    summary_text = format_run_summary(self._run_summary)
                    self.write_log(summary_text)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self.write_log(f"Could not read scan summary: {exc}")
                continue
            if line.startswith(QUOTA_MARKER):
                try:
                    quota = json.loads(line[len(QUOTA_MARKER):])
                    if isinstance(quota, dict):
                        self.update_quota_status(quota)
                    else:
                        raise ValueError("quota payload must be an object")
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self.write_log(f"Could not read OpenSubtitles allowance: {exc}")
                continue
            if line.startswith(DOWNLOAD_MARKER):
                try:
                    download = json.loads(line[len(DOWNLOAD_MARKER):])
                    if not isinstance(download, dict):
                        raise ValueError("download payload must be an object")
                    movie = str(download.get("movie") or "the movie")
                    subtitle_path = str(download.get("subtitle_path") or "")
                    message = f"Subtitle downloaded successfully for {movie}."
                    self.set_status(message)
                    self.notify(
                        f"{movie}\n{subtitle_path}" if subtitle_path else movie,
                        title="Subtitle downloaded successfully",
                        severity="information",
                        timeout=12,
                    )
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self.write_log(f"Could not read download confirmation: {exc}")
                continue
            if line.startswith("Downloaded successfully:"):
                if self._quota_info and self._quota_info.get("remaining_downloads") is not None:
                    updated = dict(self._quota_info)
                    updated["remaining_downloads"] = max(0, int(updated["remaining_downloads"]) - 1)
                    self.update_quota_status(updated, log=False)
                self.set_status("Subtitle downloaded successfully.")
                self.write_log(line)
                continue
            if line.startswith(MARKER):
                try:
                    payload = json.loads(line[len(MARKER):])
                except json.JSONDecodeError as exc:
                    self.write_log(f"Could not parse candidate list: {exc}")
                    self.process.stdin.write(b"\n")
                    await self.process.stdin.drain()
                    continue
                choice = await self.push_screen_wait(CandidateScreen(payload))
                if self._cancel_requested:
                    return
                self.process.stdin.write((f"{choice or ''}\n").encode("ascii"))
                await self.process.stdin.drain()
                if choice:
                    self.write_log(f"Selected candidate {choice} for {payload.get('movie', 'movie')}.")
                else:
                    self.write_log(f"Skipped subtitle candidates for {payload.get('movie', 'movie')}.")
                continue
            line = line.replace("Choose a result number, or Enter to skip and review later:", "")
            line = line.replace("Choose a result only after checking its movie title/year, or Enter to skip:", "")
            if line.strip():
                self.write_log(line)
                if "HTTP 429" in line or "rate or download limit reached" in line:
                    self.set_status("OpenSubtitles rejected a request (HTTP 429); check the allowance and try later.")
                    self.notify(
                        "OpenSubtitles returned HTTP 429. The request rate or download allowance may be exhausted.",
                        title="OpenSubtitles limit response",
                        severity="warning",
                        timeout=12,
                    )


def configure_utf8_console() -> None:
    """Use a Unicode-safe stream encoding on Windows consoles and pipes."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def run_tui(initial_folder: str = "") -> None:
    configure_utf8_console()
    TitleSearchApp(initial_folder=initial_folder).run()


if __name__ == "__main__":
    run_tui()
