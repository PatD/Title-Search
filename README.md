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
- `ffprobe` is optional. Without it, MP4 metadata is read directly; metadata for
  other formats may be unverified. No local speech-recognition software is used.

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
python -m pip install -r requirements.txt
```

If `py` is unavailable, use `python` in place of `py -3`.

### macOS or Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
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
not encrypted. You can instead set `OPENSUBTITLES_API_KEY`,
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

## Tests

Run the test suite from the repository folder with:

```sh
python -m unittest discover -s tests -v
```
