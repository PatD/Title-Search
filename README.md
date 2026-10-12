# Title Search

This tool was developed with OpenAI Codex.

Title Search finds or creates external `.srt` subtitles for a movie library. The
sidecar files work with Plex and Jellyfin. The app has a full-screen, keyboard-
driven Textual interface with a dark green and amber theme, and runs on Windows,
macOS, and Linux.

It searches OpenSubtitles by file hash first, then by title and year when no hash
match is available. It presents candidates for you to choose; it does not
automatically select a title-search result. Candidate details include the
catalog release, language, caption type, download count, and reported frame
rate. The app compares that frame rate with the movie and marks it as a match,
mismatch, or unknown. SRT itself has no frame-rate field, so the source video
and subtitle timing must match the same cut. Review the release and timing
before selecting a candidate. OpenSubtitles may not distinguish U.S. English
from other English variants consistently.

Movie files are read-only. Subtitles are saved beside their movies as
`Movie.en.srt` for standard English and `Movie.en.sdh.srt` for English SDH.
Existing same-folder SRT sidecars with a matching movie basename are skipped
and never overwritten. The completion status calls out when every scanned movie
already has a matching SRT.

## Install

Requirements:

- Python 3.10 or newer.
- Internet access and an OpenSubtitles.com API key to search and download
  existing subtitles.
- FFmpeg and OpenAI Whisper only if you want the optional local transcription
  fallback. Without `ffprobe`, MP4 metadata is read directly; metadata for other
  formats may be unverified.

From a terminal, clone or download this repository and change to its folder.
Using a virtual environment keeps the app's dependencies separate from other
Python projects.

### Windows PowerShell

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
   download cap if desired, and optionally enable local transcription.
3. Choose **Start scan**. If credentials are missing, enter them in the
   Credentials screen. **Search only** lets you search the catalog without
   downloading; downloads require account credentials.
4. Review candidate releases and use the arrow keys and Enter to choose one.
   Press Esc to skip candidates for that movie.

Use Tab and the arrow keys to move through controls, Enter to activate them,
Ctrl+R to start a scan, and Ctrl+Q to quit. **Clear activity** clears the log;
**Cancel scan** stops the current scan.

The activity log shows each movie's outcome and subtitle path. The final status
summarizes videos scanned, existing SRTs skipped, subtitles created, videos
without subtitles, and issues. If all movies already have matching SRT files,
the status explicitly says so. If no videos are found, the status reports that.

## Use the command-line interface

The TUI is the default. Add `--cli` to `subtitle_tool.py` to use the original
console interface and its command-line options:

```powershell
python .\subtitle_tool.py --cli "D:\Movies"
python .\subtitle_tool.py --cli "D:\Movies" --no-recursive
python .\subtitle_tool.py --cli "D:\Movies" --style sdh
python .\subtitle_tool.py --cli "D:\Movies" --style both --max-downloads 4
python .\subtitle_tool.py --cli "D:\Movies" --transcribe-missing
python .\subtitle_tool.py --cli "D:\Movies" --transcribe-missing --model medium
```

Subfolder searching is on by default; use `--no-recursive` to scan only the
selected folder. The default subtitle style is `standard`; other values are
`sdh` and `both`. The conservative free-account download cap is 10 per run.
The tool reads the account's remaining quota when available, and OpenSubtitles
enforces its actual daily quota across all runs. Searches do not count as
downloads. `--max-downloads` can lower the per-run cap.

For local transcription, install Whisper and FFmpeg separately. The first
Whisper run downloads the selected model. Transcription runs locally and may
mishear dialogue, so review generated subtitles.

## Errors and troubleshooting

- **No video files found:** Check that the selected folder contains supported
  video files. Recursive searching is enabled by default.
- **Folder not found:** Check the path. Windows accepts either slash direction;
  paths containing spaces can be pasted with or without surrounding quotes.
- **No candidates or no subtitle created:** The catalog may not have a match
  for that file or release. Check the movie filename, title/year result, cut,
  and FPS details. Enable local transcription if you want a fallback.
- **API authentication, quota, or network issue:** Check the API key and account
  credentials. Temporary GET failures are retried; service outages, rejected
  credentials, rate limits, and invalid API responses are reported in the log.
- **Whisper or FFmpeg dependency error:** Install both and ensure FFmpeg is on
  `PATH`, then restart the app.
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
