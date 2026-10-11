# Home library subtitle tool

This tool was developed with OpenAI Codex.

`subtitle_tool.py` creates external SRT sidecars for Plex and Jellyfin. It scans
a folder you choose and all its subfolders by default, leaves the movie files
untouched, and never overwrites an existing subtitle. Each SRT is saved in the
same folder as its movie. Output uses the shared naming form `Movie.en.srt` (standard
English) or `Movie.en.sdh.srt` (English SDH).

Before searching, it skips a movie when a same-folder SRT has the same basename
(`Movie.srt`, `Movie.en.srt`, `Movie.en.sdh.srt`, etc.) and prints which file
caused the skip.

SRT cues use elapsed-time timestamps; the format has no frame-rate field. The
tool searches OpenSubtitles using the file hash, which favors subtitles uploaded
for that exact file. It shows the release names and asks you to choose before
downloading, since different uploads can still have different timing or cuts.
If there is no exact hash result, it searches by title and year parsed from the
filename, shows the catalog's release name and frame rate when available, and
still waits for you to choose. It does not automatically select a different cut.
For each movie it also prints the source video frame rate and language tags
found on its tracks. When choosing a candidate, the tool labels its reported
FPS as a match, mismatch, or unknown relative to that file. MP4 files are read
only; the tool uses `ffprobe` when installed and otherwise reads MP4 sample
timing and track language metadata directly.

## Requirements

- Python 3.10 or newer.
- For the polished tables and colored status indicators, install Rich in the
  same Python environment used to run the tool: `python -m pip install rich`.
  The script still runs with plain console output if Rich is not installed.
- To download existing subtitles: an OpenSubtitles.com account and API key.
  Create the account at [OpenSubtitles](https://www.opensubtitles.com/en), then
  create an API key from your account profile as described in their
  [Getting Started guide](https://opensubtitles.tawk.help/article/getting-started).
  By default, the tool reads optional `subtitle_credentials.json` beside the
  script. Copy `subtitle_credentials.example.json` to that name and replace the
  placeholder values. `.gitignore` excludes the credential file. If the file is
  missing or a field is omitted, the tool prompts for it. Environment variables
  `OPENSUBTITLES_API_KEY`, `OPENSUBTITLES_USERNAME`, and
  `OPENSUBTITLES_PASSWORD` override the file values.
- Free-account download protection: OpenSubtitles' current help pages disagree:
  one says 20 downloads per day for a free account, while its Getting Started
  page says regular accounts get 10. The tool uses the conservative 10-download
  cap per run, reads the account's remaining quota when available, lets you
  lower the run cap with `--max-downloads`, and stops after HTTP 403/429
  responses. Search requests do not count as downloads. OpenSubtitles enforces
  the actual daily account quota, including across repeated runs.
- For the optional local fallback: OpenAI Whisper and FFmpeg installed. Whisper
  processes the movie audio locally and produces an SRT, but machine-generated
  dialogue should be reviewed for errors. The first run downloads its model.

## Examples

```powershell
python -m pip install rich
Copy-Item .\subtitle_credentials.example.json .\subtitle_credentials.json
notepad .\subtitle_credentials.json
python .\subtitle_tool.py "C:\Movies"
python .\subtitle_tool.py "D:\Library" --recursive --style both
python .\subtitle_tool.py "D:\Library" --max-downloads 4
python .\subtitle_tool.py "D:\Library" --transcribe-missing
python .\subtitle_tool.py "D:\Library" --transcribe-missing --model medium
```

The default style is standard English. `--style sdh` prefers hearing-impaired
captions; `--style both` offers each type when available. `--transcribe-missing`
enables the local fallback for movies with no selected download. A blank API key
can be entered to use local transcription only.

At the end of each run, the tool prints a per-movie report showing whether a
subtitle was downloaded, an existing SRT caused a skip, or no subtitle was
created, along with full paths for subtitle files and any relevant notes. Use
`--no-recursive` to scan only the selected folder.

The tool reports API authentication, rate-limit, service-outage, malformed
response, file-access, and output-write errors separately. A network outage,
API-key rejection, HTTP 403/429, or malformed API response stops further
catalog requests for that run. If account login returns HTTP 401, the tool
reports it and tries API-key search without an account session. Local Whisper
transcription still runs for affected movies when `--transcribe-missing` is
enabled. Title search uses a cleaned filename title and optional year after
hash search; candidate selection remains manual. SRT outputs are published
atomically and an existing sidecar is never replaced.

GET requests retry temporary network, server, and rate-limit failures up to
three times (respecting numeric `Retry-After` values); login and download POSTs
are not retried to avoid repeating account actions. When local transcription
is requested, startup checks for Whisper and FFmpeg before scanning. `ffprobe`
remains optional; without it, MP4 metadata is inspected directly and other
formats are marked unverified. A file that fails media probing is reported and
skipped. Downloaded subtitles are checked for ordered, valid cues and timings
that are reasonably close to the probed movie duration. Full-length movies must
have at least two valid cues; subtitle responses are capped at 50 MiB.
The run exits with status 1 when it encounters actionable scan or processing
errors, status 2 for startup/configuration errors, and status 130 when
interrupted. A clean run, including one with no matches, exits with status 0.

When Rich is installed, movie metadata appears in a compact panel, subtitle
candidates appear in a table with frame-rate matches and mismatches highlighted,
and the final report is a table. Candidate selection remains numbered and
interactive.

Run the resilience tests with `python -m unittest discover -s tests -v`.

The credentials JSON is plain text on this Windows account. The ignore rule
prevents Git from tracking it; it does not encrypt the file.
