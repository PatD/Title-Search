#!/usr/bin/env python3
"""Find matching English sidecar subtitles through the OpenSubtitles API.

Usage: python subtitle_tool.py [folder] [--recursive] [--style standard|sdh|both]
Existing SRTs are never overwritten. Video files are read for their
OpenSubtitles hash and media metadata; they are never changed.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # Keep the tool usable before optional presentation deps are installed.
    Console = Panel = Table = None

console = Console() if Console else None


def show(message, *, style=None):
    """Print through Rich when available, otherwise use ordinary console output."""
    if console:
        console.print(message, style=style)
    else:
        print(message)


def prompt_for_folder() -> str:
    """Present the folder prompt in the polished console when Rich is available."""
    if console:
        console.print(Panel.fit(
            "Choose the root of your movie library. The app searches that folder "
            "and its subfolders, then saves subtitle sidecars next to each movie.",
            title="[bold cyan]Home Library Subtitle Tool[/bold cyan]",
            border_style="cyan",
        ))
        return console.input("[bold cyan]Movie folder path[/bold cyan] (blank to quit): ").strip()
    print("\nHome Library Subtitle Tool")
    print("Enter the root movie folder; subfolders are searched automatically.")
    return input("Movie folder path (blank to quit): ").strip()


class ApiFailure(Exception):
    """An API failure with enough context to decide whether to stop retrying."""

    def __init__(self, message, *, status=None, outage=False):
        super().__init__(message)
        self.status = status
        self.outage = outage


class MediaProbeError(Exception):
    """The media file could not be read or has no usable media streams."""


def api_error_message(exc):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return "OpenSubtitles rejected the API key or login (HTTP 401). Check credentials."
        if exc.code == 403:
            return "OpenSubtitles denied access (HTTP 403); account quota or permission may be exhausted."
        if exc.code == 429:
            return "OpenSubtitles rate or download limit reached (HTTP 429)."
        if exc.code >= 500:
            return f"OpenSubtitles is temporarily unavailable (HTTP {exc.code})."
        return f"OpenSubtitles returned HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "OpenSubtitles request timed out."
    if isinstance(exc, urllib.error.URLError):
        return f"OpenSubtitles could not be reached: {exc.reason}"
    return str(exc)


API = "https://api.opensubtitles.com/api/v1"
API_HOSTS = {"api.opensubtitles.com", "vip-api.opensubtitles.com"}
TUI_SUMMARY_MARKER = "TITLE_SEARCH_SUMMARY:"
TUI_QUOTA_MARKER = "TITLE_SEARCH_QUOTA:"
TUI_DOWNLOAD_MARKER = "TITLE_SEARCH_DOWNLOAD:"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm"}
CHUNK = 65536
FREE_ACCOUNT_DAILY_CAP = 10
LOCAL_DAILY_WINDOW_SECONDS = 24 * 60 * 60


def opensubtitles_hash(path: Path) -> str:
    """OpenSubtitles 64-bit hash: file length plus first/last 64 KiB words."""
    size = path.stat().st_size
    value = size
    with path.open("rb") as f:
        first = f.read(CHUNK)
        f.seek(max(0, size - CHUNK))
        last = f.read(CHUNK)
    for block in (first, last):
        usable = len(block) - (len(block) % 8)
        for (word,) in __import__("struct").iter_unpack("<Q", block[:usable]):
            value = (value + word) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def mp4_boxes(f, start: int, end: int):
    """Yield (fourcc, payload_start, box_end) without loading media payloads."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) != 8:
            return
        size = int.from_bytes(head[:4], "big")
        kind = head[4:8].decode("latin-1")
        header = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) != 8:
                return
            size = int.from_bytes(ext, "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        payload = pos + header
        box_end = pos + size
        yield kind, payload, box_end
        pos = box_end


def mp4_language(f, payload: int, end: int):
    f.seek(payload)
    version_flags = f.read(4)
    if len(version_flags) != 4:
        return "und"
    version = version_flags[0]
    language_offset = payload + (32 if version == 1 else 20)
    if language_offset + 2 > end:
        return "und"
    f.seek(language_offset)
    packed = f.read(2)
    if len(packed) != 2:
        return "und"
    code = int.from_bytes(packed, "big")
    if code == 0:
        return "und"
    chars = [((code >> shift) & 0x1F) + 0x60 for shift in (10, 5, 0)]
    if any(not (0x61 <= c <= 0x7A) for c in chars):
        return "und"
    return bytes(chars).decode("ascii")


def mp4_track_info(f, trak_payload: int, trak_end: int):
    mdia = next((b for b in mp4_boxes(f, trak_payload, trak_end) if b[0] == "mdia"), None)
    if not mdia:
        return None
    _, mdia_payload, mdia_end = mdia
    children = list(mp4_boxes(f, mdia_payload, mdia_end))
    mdhd = next((b for b in children if b[0] == "mdhd"), None)
    hdlr = next((b for b in children if b[0] == "hdlr"), None)
    if not mdhd or not hdlr:
        return None
    _, mdhd_payload, mdhd_end = mdhd
    language = mp4_language(f, mdhd_payload, mdhd_end)
    f.seek(hdlr[1] + 8)
    handler = f.read(4).decode("latin-1", errors="replace")
    minf = next((b for b in children if b[0] == "minf"), None)
    fps = None
    if handler == "vide" and minf:
        stbl = next((b for b in mp4_boxes(f, minf[1], minf[2]) if b[0] == "stbl"), None)
        stts = next((b for b in mp4_boxes(f, stbl[1], stbl[2]) if b[0] == "stts"), None) if stbl else None
        if stts:
            f.seek(stts[1] + 4)
            raw_count = f.read(4)
            if len(raw_count) == 4:
                entries = int.from_bytes(raw_count, "big")
                sample_count = 0
                ticks = 0
                for _ in range(min(entries, 100000)):
                    pair = f.read(8)
                    if len(pair) != 8:
                        break
                    count = int.from_bytes(pair[:4], "big")
                    delta = int.from_bytes(pair[4:], "big")
                    sample_count += count
                    ticks += count * delta
                f.seek(mdhd_payload)
                version = f.read(1)
                f.seek(mdhd_payload + (20 if version == b"\x01" else 12))
                scale_raw = f.read(4)
                timescale = int.from_bytes(scale_raw, "big") if len(scale_raw) == 4 else 0
                if sample_count and ticks and timescale:
                    fps = sample_count * timescale / ticks
    return {"handler": handler, "language": language, "fps": fps}


def probe_mp4(path: Path) -> dict:
    """Read MP4 track FPS and language metadata using bounded box seeks."""
    info = {"fps": None, "fps_method": None, "languages": {}}
    with path.open("rb") as f:
        file_end = path.stat().st_size
        moov = next((b for b in mp4_boxes(f, 0, file_end) if b[0] == "moov"), None)
        if not moov:
            return info
        _, moov_payload, moov_end = moov
        for kind, payload, end in mp4_boxes(f, moov_payload, moov_end):
            if kind != "trak":
                continue
            track = mp4_track_info(f, payload, end)
            if not track:
                continue
            handler = track["handler"]
            info["languages"].setdefault(handler, [])
            if track["language"] not in info["languages"][handler]:
                info["languages"][handler].append(track["language"])
            if handler == "vide" and track["fps"]:
                info["fps"] = track["fps"]
                info["fps_method"] = "average from MP4 sample timing"
    return info


def probe_media(path: Path) -> dict:
    """Use ffprobe when installed; otherwise inspect MP4 metadata directly."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "format=duration:stream=codec_type,avg_frame_rate,r_frame_rate:stream_tags=language",
                 "-of", "json", str(path)], capture_output=True, text=True,
                check=True, timeout=120,
            )
            parsed = json.loads(result.stdout)
            streams = parsed.get("streams", [])
            info = {"fps": None, "fps_method": None, "languages": {},
                    "duration": None, "probe_status": "verified", "probe_error": None}
            try:
                info["duration"] = float(parsed.get("format", {}).get("duration"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            if not streams:
                raise MediaProbeError("ffprobe found no readable media streams")
            for stream in streams:
                kind = stream.get("codec_type", "unknown")
                lang = stream.get("tags", {}).get("language", "und")
                info["languages"].setdefault(kind, [])
                if lang not in info["languages"][kind]:
                    info["languages"][kind].append(lang)
                if kind == "video" and not info["fps"]:
                    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
                    try:
                        num, den = (int(x) for x in rate.split("/", 1))
                        if den:
                            info["fps"] = num / den
                            info["fps_method"] = "ffprobe average frame rate"
                    except (AttributeError, ValueError, ZeroDivisionError):
                        pass
            return info
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()
            raise MediaProbeError(detail[-1] if detail else "ffprobe could not read this media file") from exc
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise MediaProbeError(f"ffprobe failed: {exc}") from exc
    if path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        try:
            info = probe_mp4(path)
            if not info["languages"]:
                raise MediaProbeError("MP4 metadata is missing or malformed (no readable moov/tracks)")
            info.update(duration=None, probe_status="verified", probe_error=None)
            return info
        except OSError as exc:
            raise MediaProbeError(f"could not read media file: {exc}") from exc
    try:
        if path.stat().st_size == 0:
            raise MediaProbeError("media file is empty")
    except OSError as exc:
        raise MediaProbeError(f"could not inspect media file: {exc}") from exc
    return {"fps": None, "fps_method": None, "languages": {}, "duration": None,
            "probe_status": "unverified", "probe_error": "ffprobe is not installed"}


def describe_media(path: Path, info: dict):
    fps = info.get("fps")
    fps_text = f"{fps:.3f} fps ({info.get('fps_method')})" if fps else "unavailable"
    labels = {"eng": "English", "en": "English", "und": "unspecified"}
    tracks = info.get("languages", {})
    parts = []
    for kind in ("audio", "soun", "video", "vide", "subtitle", "text", "subt"):
        codes = tracks.get(kind, [])
        if codes:
            shown = ", ".join(f"{code} ({labels.get(code, code)})" for code in codes)
            parts.append(f"{kind}: {shown}")
    language_text = "; ".join(parts) if parts else "no track language tags found"
    if console:
        details = Table.grid(padding=(0, 1))
        details.add_column(style="bold cyan", justify="right")
        details.add_column()
        details.add_row("Video frame rate", fps_text)
        details.add_row("Track languages", language_text)
        details.add_row("Subtitle target", "US English (OpenSubtitles language filter: en)")
        console.print(Panel(details, title=f"[bold]{path.name}[/bold]", border_style="blue"))
    else:
        print(f"\nMEDIA: {path.name}")
        print(f"  Video frame rate: {fps_text}")
        print(f"  Track language tags: {language_text}")
        print("  Subtitle target: US English (OpenSubtitles language filter: en)")


def fps_comparison(candidate_fps, source_fps):
    if candidate_fps is None:
        return "FPS unknown"
    if source_fps is None:
        return f"{candidate_fps:g} fps; source FPS unknown"
    tolerance = max(0.05, source_fps * 0.002)
    if abs(candidate_fps - source_fps) <= tolerance:
        return f"{candidate_fps:g} fps; matches video"
    return f"{candidate_fps:g} fps; MISMATCH vs video {source_fps:.3f}"


class CredentialRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward API credentials to a different origin on redirects."""

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None] | None:
        try:
            parsed = urllib.parse.urlsplit(url)
            hostname = parsed.hostname
            if not parsed.scheme or not hostname:
                return None
            port = parsed.port
        except ValueError:
            return None
        if port is None:
            port = 443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None
        return parsed.scheme.lower(), hostname.rstrip(".").casefold(), port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        sensitive = {"api-key", "authorization"}
        has_credentials = any(name.casefold() in sensitive for name, _ in req.header_items())
        if has_credentials and self._origin(req.full_url) != self._origin(newurl):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "Refusing cross-origin redirect for a request with credentials",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(url: str, *, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    retryable = method.upper() == "GET"
    for attempt in range(3 if retryable else 1):
        try:
            opener = urllib.request.build_opener(CredentialRedirectHandler)
            with opener.open(req, timeout=45) as response:
                body = response.read(50 * 1024 * 1024 + 1)
                if len(body) > 50 * 1024 * 1024:
                    raise ValueError("OpenSubtitles response exceeded the 50 MiB safety limit")
                return body, response.headers
        except urllib.error.HTTPError as exc:
            can_retry = retryable and (exc.code == 429 or exc.code >= 500) and attempt < 2
            if can_retry:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(10, max(0, float(retry_after))) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue
            raise ApiFailure(api_error_message(exc), status=exc.code,
                             outage=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if retryable and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise ApiFailure(api_error_message(exc), outage=True) from exc


def json_object(raw: bytes, description: str) -> dict:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{description} returned an unexpected response")
    return payload


def api_items(payload: dict) -> list[dict]:
    if "data" not in payload:
        raise ValueError("Subtitle search response did not include result data")
    items = payload["data"]
    if not isinstance(items, list):
        raise ValueError("Subtitle search returned an unexpected result list")
    valid = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Subtitle search included a malformed result")
        attr = item.get("attributes")
        if not isinstance(attr, dict):
            raise ValueError("Subtitle search result did not include valid attributes")
        valid.append(item)
    return valid


def srt_payload(blob: bytes) -> bytes | None:
    """Unpack an API download (usually ZIP); return UTF-8 SRT bytes."""
    if blob.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                entries = [n for n in zf.namelist() if n.lower().endswith(".srt")]
                if not entries:
                    return None
                if zf.getinfo(entries[0]).file_size > 50 * 1024 * 1024:
                    raise ValueError("Downloaded subtitle archive contains an unexpectedly large SRT")
                return zf.read(entries[0]).decode("utf-8-sig", errors="replace").encode("utf-8")
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
            raise ValueError(f"Downloaded subtitle archive is damaged or unsupported: {exc}") from exc
    text = blob.decode("utf-8-sig", errors="replace")
    if "-->" in text:
        return text.encode("utf-8")
    return None


def valid_srt(data: bytes, duration: float | None = None) -> bool:
    text = data.decode("utf-8-sig", errors="replace")
    cue = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$")
    previous_start = -1
    latest_end = 0
    cue_count = 0
    for line in text.splitlines():
        match = cue.match(line.strip())
        if not match:
            continue
        parts = [int(value) for value in match.groups()[:8]]
        start = ((parts[0] * 60 + parts[1]) * 60 + parts[2]) * 1000 + parts[3]
        end = ((parts[4] * 60 + parts[5]) * 60 + parts[6]) * 1000 + parts[7]
        if end <= start or parts[1] > 59 or parts[2] > 59 or parts[5] > 59 or parts[6] > 59:
            return False
        if start < previous_start:
            return False
        previous_start = start
        latest_end = max(latest_end, end)
        cue_count += 1
    if previous_start < 0:
        return False
    if cue_count < 2 and (duration is None or duration > 30):
        return False
    if duration and duration > 0 and latest_end > (duration + max(2, duration * 0.02)) * 1000:
        return False
    return True


def api_headers(key: str, token: str | None = None):
    h = {"Api-Key": key, "User-Agent": "HomeLibrarySubtitleTool v1.0", "Accept": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def filter_style(items, style: str):
    if style == "sdh":
        items = [x for x in items if x["attributes"].get("hearing_impaired")]
    elif style == "standard":
        items = [x for x in items if not x["attributes"].get("hearing_impaired")]
    return items


def lookup(path: Path, key: str, token: str | None, style: str, api_base: str):
    headers = api_headers(key, token)
    digest = opensubtitles_hash(path)
    params = urllib.parse.urlencode({"languages": "en", "moviehash": digest})
    raw, _ = request(f"{api_base}/subtitles?{params}", headers=headers)
    items = filter_style(api_items(json_object(raw, "Hash search")), style)
    if items:
        return items, "exact file-hash"

    # Renamed/re-encoded files often have no indexed hash match. Search title
    # and year next, but require the user to choose a release explicitly.
    stem = path.stem
    stem = re.sub(r"\[[^\]]*(?:1080p|2160p|720p|bluray|blu-ray|web[- .]?dl|webrip|hdtv|x26[45]|hevc|proper|repack)[^\]]*\]", " ", stem, flags=re.I)
    stem = re.sub(r"\([^)]*(?:1080p|2160p|720p|bluray|blu-ray|web[- .]?dl|webrip|hdtv|x26[45]|hevc|proper|repack)[^)]*\)", " ", stem, flags=re.I)
    stem = re.sub(r"[._]+", " ", stem)
    stem = stem.strip()
    match = re.search(r"(?:\(|\[|\s)((?:19|20)\d{2})(?:\)|\]|\s*)\s*$", stem)
    year = match.group(1) if match else None
    title = re.sub(r"(?:\s*(?:\(|\[)?(?:19|20)\d{2}(?:\)|\])?\s*)$", "", stem).strip()
    # Remove common release tags while retaining meaningful words in the title.
    title = re.sub(r"\[[^\]]*\]|\([^)]*(?:1080p|2160p|720p|bluray|blu-ray|web[- .]?dl|webrip|hdtv|x26[45]|hevc|proper|repack)[^)]*\)", " ", title, flags=re.I)
    title = re.sub(r"\b(?:2160p|1080p|720p|480p|bluray|blu-ray|web[- .]?dl|webrip|hdtv|x26[45]|hevc|proper|repack)\b.*$", " ", title, flags=re.I)
    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"\s+", " ", title)
    if not title or not re.search(r"[A-Za-z0-9]", title):
        return [], "title/year (filename title unavailable)"
    query = {"languages": "en", "query": title}
    if year:
        query["year"] = year
    params = urllib.parse.urlencode(query)
    raw, _ = request(f"{api_base}/subtitles?{params}", headers=headers)
    return filter_style(api_items(json_object(raw, "Title search")), style), f"title/year: {title}" + (f" ({year})" if year else "")


def download(item, key: str, token: str | None, api_base: str) -> bytes:
    attr = item.get("attributes", {})
    files = attr.get("files", [])
    if not files:
        raise RuntimeError("Subtitle result has no downloadable file ID")
    file_id = files[0].get("file_id") if isinstance(files[0], dict) else None
    if file_id is None:
        raise ValueError("Subtitle result contains no valid downloadable file ID")
    body = json.dumps({"file_id": file_id}).encode()
    if not token:
        raise RuntimeError("Downloading requires an OpenSubtitles account. Sign in when prompted, then rerun.")
    raw, _ = request(f"{api_base}/download", method="POST", data=body,
                     headers={**api_headers(key, token), "Content-Type": "application/json"})
    link = json_object(raw, "Download request").get("link")
    if not isinstance(link, str) or not link.startswith("https://"):
        raise RuntimeError("Subtitle API did not return a download link")
    blob, _ = request(link, headers={"User-Agent": "HomeLibrarySubtitleTool v1.0"})
    result = srt_payload(blob)
    if not result or not valid_srt(result):
        raise RuntimeError("Downloaded item was not a valid SRT")
    return result


def login(key: str) -> tuple[str | None, str, dict]:
    credentials = load_credentials()
    user = os.getenv("OPENSUBTITLES_USERNAME") or credentials.get("username")
    password = os.getenv("OPENSUBTITLES_PASSWORD") or credentials.get("password")
    if os.getenv("TITLE_SEARCH_FORCE_SEARCH_ONLY") == "1":
        return None, API, {}
    if not user:
        if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
            return None, API, {}
        user = input("OpenSubtitles username (blank for search-only): ").strip()
    if not user:
        return None, API, {}
    if password is None:
        if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
            raise RuntimeError("OpenSubtitles password was not provided in the TUI credentials screen")
        password = getpass.getpass("OpenSubtitles password: ")
    raw, _ = request(f"{API}/login", method="POST",
                     data=json.dumps({"username": user, "password": password}).encode(),
                     headers={**api_headers(key), "Content-Type": "application/json"})
    session = json_object(raw, "Login")
    host = session.get("base_url", "api.opensubtitles.com")
    if not isinstance(host, str):
        raise ValueError("Login returned an invalid API host")
    url = host if "://" in host else f"https://{host}"
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Login returned an unsupported API host") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname not in API_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Login returned an unsupported API host")
    host = f"https://{parsed.hostname}"
    user_data = session.get("user", {})
    if not isinstance(user_data, dict):
        user_data = {}
    token = session.get("token")
    if token is not None and not isinstance(token, str):
        raise ValueError("Login returned an invalid session token")
    return token, host.rstrip("/") + "/api/v1", user_data


def load_credentials() -> dict:
    """Load an optional local JSON credentials file; missing file is normal."""
    path = Path(__file__).with_name("subtitle_credentials.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read credentials file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Credentials file {path} must contain a JSON object")
    for field in ("api_key", "username", "password"):
        if data.get(field) is not None and not isinstance(data[field], str):
            raise RuntimeError(f"Credentials field {field!r} in {path} must be text")
    return data


def account_limits(key: str, token: str | None, api_base: str, login_user: dict):
    """Read the account's current daily quota; return (remaining, limit, description)."""
    user = dict(login_user)
    if token:
        try:
            raw, _ = request(f"{api_base}/infos/user", headers=api_headers(key, token))
            payload = json_object(raw, "Account quota lookup").get("data", {})
            if isinstance(payload, dict):
                user.update(payload)
        except (ApiFailure, ValueError):
            pass
    limit = user.get("allowed_downloads")
    remaining = user.get("remaining_downloads")
    downloads_count = user.get("downloads_count")
    try:
        limit = int(limit) if limit is not None else None
        downloads_count = int(downloads_count) if downloads_count is not None else None
        if remaining is None and limit is not None and downloads_count is not None:
            remaining = max(0, limit - downloads_count)
        if remaining is not None:
            remaining = max(0, int(remaining))
        if limit is None and remaining is not None and downloads_count is not None:
            limit = remaining + downloads_count
    except (TypeError, ValueError, OverflowError):
        remaining = limit = None
    level = user.get("level", "account")
    if remaining is not None and limit is not None:
        description = f"{level}; {remaining} of {limit} daily subtitle download(s) remaining"
    elif remaining is not None:
        description = f"{level}; {remaining} subtitle download(s) remaining"
    else:
        description = str(level)
    return remaining, limit, description


def local_download_history_path() -> Path:
    """Return the app-local file used to enforce its rolling download cap."""
    return Path(__file__).with_name("download_history.json")


def load_recent_downloads(path: Path | None = None, *, now: float | None = None) -> list[float]:
    """Load successful downloads from the last rolling 24 hours."""
    path = path or local_download_history_path()
    now = time.time() if now is None else now
    if not path.exists():
        return []
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError("history must be a JSON list")
        recent = [float(value) for value in values]
        if any(not (0 <= value <= now) for value in recent):
            raise ValueError("history contains an invalid timestamp")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read download history {path}: {exc}") from exc
    return [value for value in recent if now - value < LOCAL_DAILY_WINDOW_SECONDS]


def save_download_history(history: list[float], path: Path | None = None) -> None:
    """Atomically persist the successful download timestamps."""
    path = path or local_download_history_path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f"{path.name}.", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(history, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def record_local_download(history: list[float], *, now: float | None = None,
                          path: Path | None = None) -> None:
    """Record a completed download and persist the rolling window."""
    now = time.time() if now is None else now
    history[:] = [value for value in history if now - value < LOCAL_DAILY_WINDOW_SECONDS]
    history.append(now)
    save_download_history(history, path)


def select_result(items, path: Path, search_kind: str, source_fps=None):
    show(f"\n{path.name}: {len(items)} English {search_kind} result(s)", style="bold")
    title_search = search_kind.startswith("title/year")
    if title_search:
        show("Title fallback (not an exact file match): verify the movie title and year before choosing.", style="yellow")
    candidate_table = Table(title="Subtitle candidates") if console else None
    tui_candidates = []
    if candidate_table:
        for heading in ("#", "Release", "FPS", "Language", "Type", "Downloads"):
            candidate_table.add_column(heading)
    for i, item in enumerate(items, 1):
        a = item.get("attributes", {})
        feature = a.get("feature_details", {})
        if not isinstance(feature, dict):
            feature = {}
        title_year = " - ".join(str(v) for v in (feature.get("title"), feature.get("year")) if v)
        identity = f"{title_year} | " if title_year else ""
        try:
            candidate_fps = float(a["fps"]) if a.get("fps") not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            candidate_fps = None
        fps = fps_comparison(candidate_fps, source_fps)
        lang = a.get("language", "en")
        release = identity + str(a.get("release", "(release unknown)"))
        sub_type = "SDH" if a.get("hearing_impaired") else "Standard"
        downloads = str(a.get("download_count", 0))
        tui_candidates.append({
            "release": release,
            "fps": fps,
            "language": lang,
            "type": sub_type,
            "downloads": downloads,
        })
        if candidate_table:
            fps_style = "red" if "MISMATCH" in fps else ("green" if "matches video" in fps else "yellow")
            candidate_table.add_row(str(i), release,
                                    f"[{fps_style}]{fps}[/{fps_style}]",
                                    f"{lang} (English; US locale not distinguished)",
                                    sub_type, downloads)
        else:
            print(f"  {i}. {release} | {fps} | language {lang} (English; US locale not distinguished by catalog) | {sub_type.lower()} | downloads {downloads}")
    if candidate_table:
        console.print(candidate_table)
    if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
        print("TITLE_SEARCH_CANDIDATES:" + json.dumps({
            "items": tui_candidates,
            "title_search": title_search,
            "movie": path.name,
            "source_fps": source_fps,
        }), flush=True)
    while True:
        try:
            prompt = ("Choose a result only after checking its movie title/year, or Enter to skip: "
                      if title_search else "Choose a result number, or Enter to skip and review later: ")
            choice = input(prompt).strip()
        except EOFError:
            print("No interactive input is available; skipping this candidate list.")
            return None
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            return items[int(choice) - 1]
        print("Enter one of the listed numbers or press Enter.")


def write_subtitle(path: Path, data: bytes, *, sdh=False):
    suffix = ".en.sdh.srt" if sdh else ".en.srt"
    output = path.with_suffix("").with_name(path.stem + suffix)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp",
                                         dir=output.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        # A same-directory hard link publishes the complete file without
        # replacing an existing sidecar, including in a concurrent run.
        os.link(temp_path, output)
    except FileExistsError:
        print(f"  Keeping existing {output.name}")
        return output
    except (AttributeError, NotImplementedError, OSError):
        # Some removable/network filesystems do not support hard links. Keep
        # the no-overwrite guarantee with exclusive creation as a fallback.
        try:
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            print(f"  Keeping existing {output.name}")
            return output
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
        except Exception:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    print(f"  Saved {output.name}")
    return output


def matching_srt_sidecar(video: Path) -> Path | None:
    """Find a same-folder SRT named exactly like this movie, optionally tagged."""
    exact = video.stem.casefold() + ".srt"
    tagged_prefix = video.stem.casefold() + "."
    for candidate in video.parent.iterdir():
        if not candidate.is_file() or candidate.suffix.casefold() != ".srt":
            continue
        name = candidate.name.casefold()
        if name == exact or name.startswith(tagged_prefix):
            return candidate
    return None


def scan_videos(root: Path, recursive: bool):
    videos = []
    warnings = []
    if recursive:
        def onerror(exc):
            warnings.append(f"Could not scan {getattr(exc, 'filename', root)}: {exc}")
        for directory, _, filenames in os.walk(root, onerror=onerror):
            for name in filenames:
                path = Path(directory) / name
                if path.suffix.lower() in VIDEO_EXTS:
                    try:
                        if path.is_file():
                            videos.append(path)
                    except OSError as exc:
                        warnings.append(f"Could not inspect {path}: {exc}")
    else:
        try:
            for path in root.iterdir():
                try:
                    if path.suffix.lower() in VIDEO_EXTS and path.is_file():
                        videos.append(path)
                except OSError as exc:
                    warnings.append(f"Could not inspect {path}: {exc}")
        except OSError as exc:
            warnings.append(f"Could not scan {root}: {exc}")
    return sorted(videos), warnings


def preflight() -> list[str]:
    """Check required runtime dependencies before scanning."""
    problems = []
    if sys.version_info < (3, 10):
        problems.append("Python 3.10 or newer is required.")
    return problems


def emit_tui_summary(summary: dict) -> None:
    """Send a compact machine-readable completion summary to the Textual UI."""
    if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
        print(f"{TUI_SUMMARY_MARKER}{json.dumps(summary, separators=(',', ':'))}", flush=True)


def emit_tui_quota(quota: dict) -> None:
    """Send account quota details to the Textual UI without parsing display text."""
    if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
        print(f"{TUI_QUOTA_MARKER}{json.dumps(quota, separators=(',', ':'))}", flush=True)


def emit_tui_download(movie: str, subtitle_path: str) -> None:
    """Notify the Textual interface about a completed subtitle download."""
    if os.getenv("TITLE_SEARCH_TUI_CHILD") == "1":
        payload = {"movie": movie, "subtitle_path": subtitle_path}
        print(f"{TUI_DOWNLOAD_MARKER}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", type=Path, help="folder to scan (prompted for when omitted)")
    parser.add_argument("--recursive", dest="recursive", action="store_true", help="scan subfolders (default)")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false", help="scan only the selected folder")
    parser.set_defaults(recursive=True)
    parser.add_argument("--style", choices=["standard", "sdh", "both"], default="standard")
    parser.add_argument("--max-downloads", type=int, default=FREE_ACCOUNT_DAILY_CAP,
                        help="maximum subtitle downloads during this run (1-10 conservative free-account cap; default: 10)")
    args = parser.parse_args()
    if args.folder is None:
        try:
            folder = prompt_for_folder()
        except EOFError:
            folder = ""
        if not folder:
            show("No folder path provided. Exiting.", style="yellow")
            return 0
        # Accept paths pasted from Windows Explorer, which are often surrounded by quotes.
        args.folder = Path(folder.strip('"'))
    dependency_problems = preflight()
    if dependency_problems:
        for problem in dependency_problems:
            print(f"Dependency error: {problem}", file=sys.stderr)
        return 2
    try:
        root = args.folder.expanduser().resolve()
    except OSError as exc:
        print(f"Could not resolve folder {args.folder}: {exc}", file=sys.stderr)
        return 2
    if not root.is_dir():
        parser.error(f"not a folder: {root}")
    if not 1 <= args.max_downloads <= FREE_ACCOUNT_DAILY_CAP:
        parser.error(f"--max-downloads must be between 1 and {FREE_ACCOUNT_DAILY_CAP} for free-account mode")
    try:
        videos, scan_warnings = scan_videos(root, args.recursive)
    except OSError as exc:
        print(f"Could not scan {root}: {exc}", file=sys.stderr)
        return 2
    for warning in scan_warnings:
        show(f"Warning: {warning}", style="yellow")
    if not videos:
        show(f"No supported video files found in {root} or its subfolders.", style="yellow")
        emit_tui_summary({"videos_found": 0, "ignored_existing": 0, "outputs_created": 0,
                          "without_subtitles": 0, "failures": 0, "warnings": len(scan_warnings)})
        return 1 if scan_warnings else 0
    if console:
        console.print(Panel.fit(
            f"[bold]{len(videos)} video(s)[/bold] found under [cyan]{root}[/cyan]\n"
            f"Recursive scan: {'on' if args.recursive else 'off'}  |  Style: {args.style}",
            title="Home Library Subtitles", border_style="cyan"))
    else:
        print(f"Found {len(videos)} video(s) under {root}; recursive scan {'on' if args.recursive else 'off'}; style {args.style}.")
    try:
        credentials = load_credentials()
    except (OSError, RuntimeError) as exc:
        print(f"Could not load API credentials: {exc}", file=sys.stderr)
        return 2
    key = None if os.getenv("TITLE_SEARCH_DISABLE_API") == "1" else (
        os.getenv("OPENSUBTITLES_API_KEY") or credentials.get("api_key")
    )
    startup_note = None
    if not key and os.getenv("TITLE_SEARCH_TUI_CHILD") != "1":
        try:
            key = getpass.getpass("OpenSubtitles API key (blank to continue without API search): ")
        except EOFError:
            key = ""
            startup_note = "No interactive input was available; continuing without OpenSubtitles."
    token, api_base, login_user = None, API, {}
    api_active = bool(key)
    if key:
        try:
            token, api_base, login_user = login(key)
        except ApiFailure as exc:
            startup_note = api_error_message(exc)
            if exc.outage:
                api_active = False
            elif exc.status in (401, 403):
                # Search may still work with an API key even when account login fails.
                token = None
            else:
                api_active = False
        except EOFError:
            startup_note = "No interactive input was available for account login; using API-key search only."
            token = None
        except (ValueError, RuntimeError, OSError) as exc:
            startup_note = f"OpenSubtitles login failed: {exc}"
            api_active = False
    try:
        remaining, daily_limit, account_description = (
            account_limits(key, token, api_base, login_user)
            if key and api_active else
            (None, None, "API unavailable" if key else "no API key provided")
        )
    except (TypeError, ValueError, OverflowError):
        remaining, daily_limit, account_description = None, None, "quota unavailable"
    history_error = None
    try:
        download_history = load_recent_downloads()
    except RuntimeError as exc:
        # If local history cannot be trusted, block downloads rather than bypass the cap.
        download_history = [time.time()] * FREE_ACCOUNT_DAILY_CAP
        history_error = str(exc)
    local_remaining = max(0, FREE_ACCOUNT_DAILY_CAP - len(download_history))
    limits = [args.max_downloads, local_remaining]
    if remaining is not None:
        limits.append(remaining)
    budget = min(limits)
    show(f"OpenSubtitles account: {account_description}. This run is capped at {budget} download(s).", style="dim")
    emit_tui_quota({
        "remaining_downloads": remaining,
        "daily_limit": daily_limit,
        "local_remaining_downloads": local_remaining,
        "local_daily_limit": FREE_ACCOUNT_DAILY_CAP,
        "per_run_cap": args.max_downloads,
        "account": account_description,
    })
    if history_error:
        show(f"Local download history could not be read; downloads are disabled for safety: {history_error}", style="yellow")
    quota_exhausted = budget == 0
    if quota_exhausted:
        show("The 24-hour download allowance is exhausted. This scan will check local files without searching for downloads.", style="yellow")
    if startup_note:
        show(f"OpenSubtitles startup issue: {startup_note}", style="yellow")
    download_attempts = 0
    successful_downloads = 0
    report = []
    stop_reason = None
    if key and not api_active:
        stop_reason = startup_note or "OpenSubtitles could not be initialized."
    for video in videos:
        row = {"movie": str(video), "status": "", "outputs": [], "notes": [], "api_failed": False}
        report.append(row)
        if startup_note:
            row["notes"].append(startup_note)
            row["api_failed"] = bool(key)
        try:
            existing_srt = matching_srt_sidecar(video)
        except OSError as exc:
            row["notes"].append(f"Could not inspect neighboring subtitle files: {exc}")
            row["status"] = "Media error"
            continue
        if existing_srt:
            print(f"Ignoring {video.name}: matching subtitle already exists ({existing_srt.name}).")
            row["status"] = "Ignored (existing SRT)"
            try:
                row["outputs"].append(str(existing_srt.resolve()))
            except OSError as exc:
                row["notes"].append(f"Could not resolve existing subtitle path: {exc}")
            continue
        try:
            media_info = probe_media(video)
        except MediaProbeError as exc:
            message = f"Media file is unreadable or malformed: {exc}"
            row["notes"].append(message)
            row["status"] = "File access error"
            print(f"{video.name}: {message}")
            continue
        except (ValueError, TypeError, AttributeError) as exc:
            media_info = {"fps": None, "fps_method": None, "languages": {}}
            row["notes"].append(f"Media metadata could not be read: {exc}")
        if media_info.get("probe_status") == "unverified":
            row["notes"].append("Media could not be fully verified because ffprobe is unavailable; metadata may be incomplete.")
        try:
            describe_media(video, media_info)
        except Exception as exc:
            row["notes"].append(f"Could not display media details: {type(exc).__name__}: {exc}")
            print(f"{video.name}: could not display media details: {exc}")
        source_fps = media_info.get("fps")
        downloaded = 0
        if api_active and not quota_exhausted:
            try:
                matches, search_kind = lookup(video, key, token, args.style, api_base)
                if args.style == "both":
                    any_candidates = False
                    for hearing in (False, True):
                        candidates = [x for x in matches if bool(x.get("attributes", {}).get("hearing_impaired")) == hearing]
                        if candidates:
                            any_candidates = True
                            selected = select_result(candidates, video, search_kind, source_fps)
                            if selected:
                                if download_attempts >= budget:
                                    print("Free-account download budget reached; skipping this download.")
                                    row["notes"].append("Download skipped because the free-tier run budget was reached.")
                                else:
                                    download_attempts += 1
                                    data = download(selected, key, token, api_base)
                                    if not valid_srt(data, media_info.get("duration")):
                                        raise RuntimeError("Downloaded subtitle cues are malformed or extend well beyond the video runtime")
                                    written = write_subtitle(video, data, sdh=hearing)
                                    if written:
                                        written_path = str(written.resolve())
                                        row["outputs"].append(written_path)
                                        downloaded += 1
                                        successful_downloads += 1
                                        record_local_download(download_history)
                                        print(f"Downloaded successfully: {written_path}", flush=True)
                                        emit_tui_download(video.name, written_path)
                            else:
                                row["notes"].append(f"User skipped the {'SDH' if hearing else 'standard'} candidate list.")
                    if not any_candidates:
                        row["notes"].append("No title/hash candidate found for the selected style.")
                elif matches:
                    selected = select_result(matches, video, search_kind, source_fps)
                    if selected:
                        hearing = bool(selected.get("attributes", {}).get("hearing_impaired"))
                        if download_attempts >= budget:
                            print("Free-account download budget reached; skipping this download.")
                            row["notes"].append("Download skipped because the free-tier run budget was reached.")
                        else:
                            download_attempts += 1
                            data = download(selected, key, token, api_base)
                            if not valid_srt(data, media_info.get("duration")):
                                raise RuntimeError("Downloaded subtitle cues are malformed or extend well beyond the video runtime")
                            written = write_subtitle(video, data, sdh=hearing)
                            if written:
                                written_path = str(written.resolve())
                                row["outputs"].append(written_path)
                                downloaded += 1
                                successful_downloads += 1
                                record_local_download(download_history)
                                print(f"Downloaded successfully: {written_path}", flush=True)
                                emit_tui_download(video.name, written_path)
                    else:
                        row["notes"].append("User skipped the candidate list.")
                else:
                    print(f"{video.name}: no English subtitle candidates found by hash or title/year")
                    row["notes"].append(f"No English candidate found by file hash or {search_kind}.")
            except ApiFailure as exc:
                message = api_error_message(exc)
                print(f"{video.name}: {message}")
                row["notes"].append(message)
                row["api_failed"] = True
                row["status"] = "API error"
                if exc.outage or exc.status in (401, 403, 429):
                    api_active = False
                    stop_reason = message
            except (ValueError, KeyError, AttributeError, zipfile.BadZipFile) as exc:
                message = f"OpenSubtitles returned invalid or incomplete data: {exc}"
                print(f"{video.name}: {message}")
                row["notes"].append(message)
                row["api_failed"] = True
                row["status"] = "API error"
                # Repeatedly querying a service that is returning malformed data is unlikely to help.
                api_active = False
                stop_reason = message
            except (OSError, RuntimeError) as exc:
                message = f"Subtitle processing failed: {exc}"
                print(f"{video.name}: {message}")
                row["notes"].append(message)
                row["status"] = "File or download error"
            except Exception as exc:
                message = f"Unexpected per-movie API processing error: {type(exc).__name__}: {exc}"
                print(f"{video.name}: {message}")
                row["notes"].append(message)
                row["api_failed"] = True
                row["status"] = "API error"
                api_active = False
                stop_reason = message
        elif quota_exhausted:
            row["notes"].append("OpenSubtitles daily download allowance is exhausted; subtitle search was skipped.")
            row["status"] = "Download quota exhausted"
        elif not key:
            row["notes"].append("OpenSubtitles credentials were not provided.")
        elif stop_reason:
            row["notes"].append(f"Not searched because OpenSubtitles is unavailable: {stop_reason}")
            row["api_failed"] = True
            row["status"] = "API unavailable"

        # Keep one unexpected movie-level problem from aborting the remaining scan.
        if not row["status"] and row["notes"] and any("error" in note.lower() for note in row["notes"]):
            row["status"] = "File or processing error"

        if row["outputs"]:
            if downloaded:
                row["status"] = "Downloaded"
            elif row["api_failed"]:
                row["status"] = "Completed with API issue"
            else:
                row["status"] = "Completed"
        elif row["api_failed"] and row["status"] != "File or download error":
            row["status"] = "API unavailable" if not api_active else "API error"
        elif not row["status"]:
            row["status"] = "No match" if any("No English candidate" in n for n in row["notes"]) else "No subtitle created"

    if console:
        console.print(Panel.fit(
            f"Movies found: [bold]{len(videos)}[/bold]  |  Download attempts: [bold]{download_attempts}/{budget}[/bold]",
            title="Run complete", border_style="green"))
        report_table = Table(title="Subtitle run report", show_lines=True)
        report_table.add_column("Result", style="bold", no_wrap=True)
        report_table.add_column("Movie", overflow="fold")
        report_table.add_column("Subtitle path(s)", overflow="fold")
        report_table.add_column("Notes", overflow="fold")
        for row in report:
            status = row["status"]
            status_style = "green" if status in {"Downloaded", "Completed"} else ("yellow" if status.startswith("Ignored") or status in {"No match", "No subtitle created"} else "red")
            report_table.add_row(f"[{status_style}]{status}[/{status_style}]", row["movie"],
                                 "\n".join(row["outputs"]) or "—",
                                 "\n".join(row["notes"]) or "—")
        console.print(report_table)
    else:
        print("\n=== Run report ===")
        print(f"Movies found: {len(videos)} | Subtitle download attempts: {download_attempts}/{budget}")
        for row in report:
            print(f"\n{row['status']}: {row['movie']}")
            for output in row["outputs"]:
                print(f"  Subtitle/location: {output}")
            for note in row["notes"]:
                print(f"  Note: {note}")
    failures = {"API error", "API unavailable", "File access error", "Media error", "File or download error",
                "File or processing error", "Completed with API issue"}
    ignored_existing = sum(row["status"] == "Ignored (existing SRT)" for row in report)
    outputs_created = sum(
        len(row["outputs"])
        for row in report
        if row["status"] != "Ignored (existing SRT)"
    )
    emit_tui_summary({
        "videos_found": len(videos),
        "ignored_existing": ignored_existing,
        "outputs_created": outputs_created,
        "downloaded_subtitles": successful_downloads,
        "without_subtitles": sum(not row["outputs"] and row["status"] not in failures for row in report),
        "failures": sum(row["status"] in failures for row in report),
        "warnings": len(scan_warnings),
    })
    return 1 if scan_warnings or any(row["status"] in failures for row in report) else 0


def launch_tui(initial_folder: str = ""):
    try:
        from title_search_tui import TitleSearchApp
    except ImportError as exc:
        print("The full-screen interface needs Textual. Install project dependencies with: python -m pip install -r requirements.txt", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        raise SystemExit(2)
    TitleSearchApp(initial_folder=initial_folder).run()


if __name__ == "__main__":
    try:
        if "--cli" in sys.argv[1:]:
            sys.argv.remove("--cli")
            raise SystemExit(main())
        initial = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else ""
        launch_tui(initial)
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        raise SystemExit(2)
