# Title Search

This tool was developed with OpenAI Codex.

Title Search finds and downloads external `.srt` subtitles for a movie library. The
sidecar files work with Plex and Jellyfin. The app has a full-screen, keyboard-
driven Textual interface with a dark green and amber theme, and runs on Windows,
macOS, and Linux.

It searches OpenSubtitles by file hash first, then by title and year when no hash
match is available. It presents candidates for you to choose; it does not
automatically select a title-search result. Candidate details include the
catalog release, language, caption type, download count, and reported frame
rate. The app compares that frame rate with the movie and marks it as a match,
mismatch, or unknown to help you choose. It does not automatically reject an
FPS mismatch. SRT itself has no frame-rate field, so the source video and
subtitle timing must match the same cut. Review the release, cut, and FPS
before selecting a candidate. The search uses OpenSubtitles' English (`en`)
filter; its catalog may not distinguish U.S. English from other English
variants consistently, so verify the release when that distinction matters.

Movie files are read-only. Subtitles are saved beside their movies as
`Movie.en.srt` for standard English and `Movie.en.sdh.srt` for English SDH.
Existing same-folder SRT sidecars with a matching movie basename are skipped
and never overwritten. The completion status calls out when every scanned movie
already has a matching SRT.

## Quick start for end users

### Windows: double-click to run

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/).
   Keep the Python Launcher (`py`) option enabled if the installer offers it.
2. Download this repository as a ZIP from GitHub and extract the folder, or clone
   the repository with Git.
3. Double-click **Start Title Search.bat** in the extracted folder. On its first
   launch it creates a private `.venv` environment and installs the app's Python
   packages. This setup needs an internet connection; later launches reuse the
   environment.
4. In the app, choose **Credentials** and enter your OpenSubtitles API key,
   username, and password. To avoid entering them again, follow the saved
   credentials instructions below.
5. Enter the folder containing your movies, choose the subtitle style, and click
   **Start scan**. Review each proposed release and choose it with the arrow keys
   and Enter, or press Esc to skip it.

The batch file leaves its window open when the app exits so startup errors remain
visible. If Python is already installed, you do not need to open a terminal or
install packages manually.

### macOS or Linux

Install Python 3.10 or newer, download or clone the repository, then follow the
manual virtual-environment instructions below. Launch with
`python3 title_search_tui.py` from the repository folder.

## Install

Requirements for manual installation:

- Python 3.10 or newer.
- Internet access and an OpenSubtitles.com API key to search and download
  existing subtitles.
- `ffprobe` is optional. When present on `PATH`, it reads metadata from scanned
  media files with your account's permissions. Keep it updated and scan only
  media you trust. Without it, MP4 metadata is read directly; metadata for other
  formats may be unverified. No local speech-recognition software is used.

Using a virtual environment keeps the app's dependencies separate from other
Python projects. The Windows batch launcher creates and uses this environment
automatically.

### Windows PowerShell

For the easiest Windows launch, double-click `Start Title Search.bat` in
File Explorer. The first launch creates a `.venv` folder and installs the
Python dependencies; later launches reuse that environment. The launcher keeps
the terminal window open after the app exits so any error remains visible.
Python 3.10 or newer and an internet connection are needed for initial setup.

You can also install and launch manually in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.txt
```

If `py` is unavailable, use `python` in place of `py -3`.

### macOS or Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.txt
```

### OpenSubtitles credentials

Create an account at [OpenSubtitles](https://www.opensubtitles.com/en) and
create an API key from your account profile using their
[Getting Started guide](https://opensubtitles.tawk.help/article/getting-started).
The API key enables searching. An account username and password are also needed
to download subtitles.

To keep credentials between runs, copy the example file beside the scripts and
edit its values:

```powershell
Copy-Item .\subtitle_credentials.example.json .\subtitle_credentials.json
notepad .\subtitle_credentials.json
```

On macOS or Linux, use:

```sh
cp subtitle_credentials.example.json subtitle_credentials.json
nano subtitle_credentials.json
```

The JSON file accepts `api_key`, `username`, and `password`. It is excluded by
`.gitignore`, so Git will not add it to commits. The file is plain text and is
not encrypted. On Windows, use the file's **Properties → Security → Advanced**
settings to remove broad inherited access and grant access only to your account
(and Windows system administrators). On macOS or Linux, restrict it with
`chmod 600 subtitle_credentials.json`. Treat backups and synced copies as
credential copies too. Environment variables avoid storing values in this file,
but they are not a secure vault and may be visible to processes running as your
account. You can instead set `OPENSUBTITLES_API_KEY`,
`OPENSUBTITLES_USERNAME`, and `OPENSUBTITLES_PASSWORD` environment variables;
they override values in the JSON file. The TUI's Credentials screen accepts
values for the current run but does not save them.

## Use the TUI

Activate the virtual environment, then launch either entry point:

```powershell
python .\title_search_tui.py
```

or:

```powershell
python .\subtitle_tool.py
```

Use the equivalent `python title_search_tui.py` or `python subtitle_tool.py` on
macOS and Linux. You may pass a folder to `subtitle_tool.py` to prefill the
library path field:

```powershell
python .\subtitle_tool.py "D:\Movies"
```

In the interface:

1. Enter the folder that contains your movies. The app searches subfolders by
   default. Windows paths accept either `/` or `\` separators.
2. Choose Standard English, English SDH, or Offer both. Set the per-run
   download cap if desired.
3. Choose **Start scan**. If credentials are missing, enter them in the
   Credentials screen. **Search only** lets you search the catalog without
   downloading; downloads require account credentials.
4. Review candidate releases and use the arrow keys and Enter to choose one.
   Press Esc to skip candidates for that movie.

The app prefers exact file-hash results, then falls back to title/year search.
Title/year results require extra care because they are not tied to the exact
video file. Candidate FPS is compared with the video FPS when available; a
mismatch is shown as a warning, but the choice remains yours. The scan also
displays track language metadata when it can be read. Video language tracks do
not determine the subtitle language requested from OpenSubtitles.

Use Tab and the arrow keys to move through controls, Enter to activate them,
Ctrl+R to start a scan, and Ctrl+Q to quit. **Clear activity** clears the log;
**Cancel scan** stops the current scan.

The activity log shows each movie's outcome and subtitle path. The final status
summarizes videos scanned, existing SRTs skipped, subtitles created, videos
without subtitles, and issues. If all movies already have matching SRT files,
the status explicitly says so. If no videos are found, the status reports that.
Successful downloads trigger an on-screen confirmation naming the movie and
showing the saved subtitle path. The allowance panel reports the account's
remaining daily downloads when OpenSubtitles provides that information; scans
themselves do not consume the download allowance.

## Use the command-line interface

The TUI is the default. Add `--cli` to `subtitle_tool.py` to use the original
console interface and its command-line options:

```powershell
python .\subtitle_tool.py --cli "D:\Movies"
python .\subtitle_tool.py --cli "D:\Movies" --no-recursive
python .\subtitle_tool.py --cli "D:\Movies" --style sdh
python .\subtitle_tool.py --cli "D:\Movies" --style both --max-downloads 4
```

Subfolder searching is on by default; use `--no-recursive` to scan only the
selected folder. The default subtitle style is `standard`; other values are
`sdh` and `both`. The conservative free-account download cap is 10 per run.
The tool reads the account's remaining quota when available, and OpenSubtitles
enforces its actual daily quota across all runs. Searches do not count as
downloads. `--max-downloads` can lower the per-run cap.

## Errors and troubleshooting

- **No video files found:** Check that the selected folder contains supported
  video files. Recursive searching is enabled by default.
- **Folder not found:** Check the path. Windows accepts either slash direction;
  paths containing spaces can be pasted with or without surrounding quotes.
- **No candidates or no subtitle created:** The catalog may not have a match
  for that file or release. Check the movie filename, title/year result, cut,
  and FPS details. This tool only downloads existing subtitles from the
  OpenSubtitles API; it does not generate subtitles.
- **API authentication, quota, or network issue:** Check the API key and account
  credentials. Temporary GET failures are retried; service outages, rejected
  credentials, rate limits, and invalid API responses are reported in the log.
- **Media probe issue:** `ffprobe` is optional. MP4 metadata can be inspected
  directly when it is unavailable; other formats may be marked unverified.

Downloaded subtitles are checked for valid, ordered cues and timing close to
the movie runtime. Output is written atomically, and an existing sidecar is
never replaced. The CLI exits with status 1 for scan or processing errors,
status 2 for startup/configuration errors, and status 130 when interrupted.
Subtitle files come from an external catalog. This app does not execute them,
but media players parse their contents when you play a movie. Keep your media
player updated and treat downloaded subtitle files as untrusted input.

## Dependency security (maintainers)

`requirements.txt` pins the runtime packages and their universal wheel hashes.
The Windows launcher and manual install commands verify those hashes. Review
dependency updates before changing a version or its hash; the lock includes all
runtime and transitive packages. To audit the locked dependencies without
adding the audit tool to the app environment, create a separate environment:

```powershell
py -3 -m venv .audit-venv
.\.audit-venv\Scripts\python.exe -m pip install pip-audit
.\.audit-venv\Scripts\pip-audit.exe -r requirements.txt
```

On macOS or Linux, use:

```sh
python3 -m venv .audit-venv
.audit-venv/bin/python -m pip install pip-audit
.audit-venv/bin/pip-audit -r requirements.txt
```

## Tests

Run the test suite from the repository folder with:

```sh
python -m unittest discover -s tests -v
```

The tests use Python's built-in `unittest` framework. Network requests, subprocesses,
filesystem errors, and user input are mocked where practical, so the suite does not
need an OpenSubtitles account, a network connection, or FFmpeg. Temporary files are
used for media, subtitle, and folder checks.

### `tests/test_resilience.py`

These tests cover subtitle-engine behavior, input validation, and failure handling:

- `test_tui_summary_emits_machine_readable_line_only_for_ui_child` checks that scan
  summaries are emitted in the UI's JSON marker format only for UI child processes.
- `test_tui_download_confirmation_emits_movie_and_path_for_ui_child` checks that
  download notifications include the movie and saved path only for UI child processes.
- `test_get_request_retries_service_error_then_succeeds` verifies that a retryable
  HTTP service error is retried and a later successful response is returned.
- `test_post_request_is_not_retried` verifies that a failed POST is attempted only once.
- `test_retries_timeouts_only_three_times` verifies the request retry limit and that
  a persistent timeout is reported as an API outage.
- `test_authenticated_redirects_are_limited_to_the_same_origin` verifies that API
  credentials are retained for same-origin redirects and rejected for cross-origin ones.
- `test_unauthenticated_download_redirects_remain_supported` verifies that subtitle
  file downloads can follow redirects without forwarding API credentials.
- `test_login_accepts_documented_api_hosts` checks login with both supported
  OpenSubtitles API hosts.
- `test_login_rejects_unsupported_api_hosts` checks rejection of unsafe or malformed
  API base URLs, including HTTP, unexpected ports, user information, and lookalike hosts.
- `test_runtime_requirements_are_exact_and_hash_pinned` checks that runtime packages
  are exactly pinned and each has a SHA-256 hash under hash-checking mode.
- `test_corrupt_mp4_is_reported` verifies that an invalid MP4 is rejected when no
  media probe is available.
- `test_ffprobe_failure_is_reported_as_corrupt_media` verifies that an `ffprobe`
  failure is surfaced as a media validation error.
- `test_dependency_preflight_requires_supported_python_only` checks the minimum
  supported Python version error.
- `test_dependency_preflight_accepts_supported_python_without_whisper_or_ffmpeg`
  verifies that supported Python is sufficient and optional Whisper/FFmpeg tools are
  not required for startup.
- `test_srt_checks_cues_order_and_runtime` checks valid cue timing, chronological
  ordering, and subtitle timing against the media runtime.
- `test_bad_subtitle_zip_becomes_clear_validation_error` checks that a damaged
  subtitle archive produces a clear validation error.
- `test_scanning_skips_broken_symlinks_and_records_permission_errors` checks that
  inaccessible files do not stop a scan and that a warning is recorded.
- `test_output_failure_does_not_create_outside_target` checks that a failed subtitle
  write leaves no partial subtitle file behind.
- `test_no_path_prompts_then_exits_when_input_is_blank` checks the empty-path prompt
  and clean exit behavior.
- `test_folder_prompt_uses_rich_console_when_available` checks that folder entry uses
  the Rich console when it is available.
- `test_interactive_folder_with_no_videos_reports_empty_library` checks the result
  shown when the selected folder contains no supported videos.

### `tests/test_tui.py`

These tests cover the Textual interface, scan lifecycle, and subtitle candidate picker:

- `test_app_exposes_title_palette_and_keyboard_actions` checks the app title, key
  bindings, core colors, and launcher's UTF-8 and hash-locked installation settings.
- `test_command_palette_hides_screenshot_and_theme_commands` checks which system
  commands appear in the command palette.
- `test_ascii_gradient_uses_a_background_layer` checks the ASCII gradient dimensions
  and that it is composed as a background layer behind the interface.
- `test_folder_input_accepts_both_windows_separator_styles` checks that Windows paths
  using either slash direction resolve to the same folder. This test runs only on Windows.
- `test_scan_and_credentials_events_are_dispatched_as_workers` checks that scan and
  credential actions are dispatched as background workers.
- `test_launch_without_folder_reports_error_and_does_not_start_worker` checks that a
  blank folder path is reported and does not start a scan.
- `test_download_summary_preserves_other_scan_results` checks that download results
  are added without losing the rest of the scan summary.
- `test_quota_status_estimates_download_batches_and_separates_scans` checks remaining
  download estimates and clarifies that scanning does not use download quota.
- `test_quota_status_reports_exhaustion_and_unavailable_account` checks the messages
  for an exhausted quota and an account whose quota could not be retrieved.
- `test_download_confirmation_updates_status_and_logs_path` checks that a successful
  download updates the status and logs the saved path.
- `test_download_confirmation_notifies_with_movie_name_and_saved_path` checks that the
  success notification includes the movie name and subtitle path.
- `test_scan_indicator_animates_and_can_be_hidden` checks that the scan indicator
  advances through frames and disappears when scanning stops.
- `test_scan_ui_locks_configuration_and_leaves_cancel_enabled` checks that scan
  settings are disabled during a scan while the cancel control remains available.
- `test_cancel_scan_terminates_process_and_reports_status` checks that cancellation
  requests terminate the worker and update the status and log.
- `test_subprocess_startup_failure_is_reported_and_controls_reenabled` checks error
  reporting and control restoration when the subtitle process cannot start.
- `test_repeated_scan_after_unexpected_error_restores_ui` checks that an unexpected
  worker error restores the interface and a later scan can still complete.
- `test_candidate_label_includes_release_fps_language_and_match_style` checks the
  release, frame rate, language, download count, and visual match style in a candidate.
- `test_candidate_mismatch_is_visually_flagged` checks the visible warning for a
  candidate with a mismatched frame rate.
- `test_candidate_selection_returns_one_based_result_number` checks that selecting a
  candidate returns its one-based result number to the scan flow.
- `test_candidate_skip_returns_no_selection` checks that skipping returns no selection.
- `test_candidate_picker_keeps_cancel_and_quit_available` checks that cancel and quit
  remain available from the candidate picker.
- `test_run_summary_calls_out_when_every_movie_already_has_srt` checks the summary
  shown when every video already has a subtitle file.
- `test_run_summary_reports_empty_and_partial_scans` checks the summaries for empty
  libraries and scans with existing subtitles, outputs, and issues.
