#!/usr/bin/env python3
"""Transcribe a video via any OpenAI-compatible Whisper endpoint.

Strategy: extract audio (mono 16kHz mp3, tiny payload), upload to whichever
provider has a key. Returns segments in the same shape as transcribe.parse_vtt
so the rest of the pipeline (filter_range, format_transcript) doesn't care
where the transcript came from.

Bring-your-own-provider: three presets ship by default (groq, openai, nan),
but every field is overridable by env var and you can register your own named
backend. See BACKENDS / _resolve_backend below and PROVIDERS.md.

  WATCH_<NAME>_ENDPOINT   transcription URL (…/audio/transcriptions)
  WATCH_<NAME>_MODEL      model id the provider expects
  WATCH_<NAME>_KEY_FILE   optional file to read the API key from (0600)
  WATCH_<NAME>_AUTH=none  auth-less local servers (Ollama, LAN faster-whisper)
  <NAME>_API_KEY          the key itself (env or ~/.config/watch/.env)
  WATCH_EXTRA_PROVIDERS   comma list of extra backend names to register

Pure stdlib — no `pip install groq` / `openai` / etc. needed.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


# Presets. Every value here is a default; env vars override per-field.
#   limits.max_upload_mb .... refuse/segment uploads above this
#   limits.chunk_seconds .... split long audio into pieces this big (0 = never)
#   limits.min_interval_s ... minimum seconds between uploads (0 = no pacing)
# groq/openai run server-side GPUs: no client-side caps needed.
BACKENDS: dict[str, dict] = {
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
        "key_env": "GROQ_API_KEY",
        "limits": {},
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
        "key_env": "OPENAI_API_KEY",
        "limits": {},
    },
    # NaN Builders: community cluster. whisper-large-v3 under the bare id "whisper",
    # behind a proxy documented at ~1x realtime with a 25 MB upload cap and 10 rpm
    # (https://nan.builders/docs/models#whisper). The limits below make the client
    # honour those so long caption-less audio still transcribes (auto-segmented).
    "nan": {
        "endpoint": "https://api.nan.builders/v1/audio/transcriptions",
        "model": "whisper",
        "key_env": "NAN_API_KEY",
        "limits": {"max_upload_mb": 24, "chunk_seconds": 110, "min_interval_s": 6.5},
    },
}

# Resolution order when no backend is forced. First key found wins.
DEFAULT_PREFERENCE = ["groq", "openai", "nan"]


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _extra_providers() -> list[str]:
    raw = _env("WATCH_EXTRA_PROVIDERS") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _register_extra_providers() -> None:
    """Add user-defined backends from WATCH_EXTRA_PROVIDERS.

    A provider named `foo` is configured entirely by env:
      WATCH_FOO_ENDPOINT (required), WATCH_FOO_MODEL (required), FOO_API_KEY,
      optional WATCH_FOO_KEY_FILE / WATCH_FOO_AUTH / WATCH_FOO_MAX_UPLOAD_MB /
      WATCH_FOO_CHUNK_SECONDS / WATCH_FOO_MIN_INTERVAL_S.
    Lets --whisper foo work for Ollama, faster-whisper servers, vLLM, ElevenLabs,
    a company gateway — anything speaking the OpenAI audio/transcriptions shape.
    """
    for name in _extra_providers():
        if name in BACKENDS:
            continue
        up = name.upper()
        endpoint = _env(f"WATCH_{up}_ENDPOINT")
        model = _env(f"WATCH_{up}_MODEL")
        if not endpoint or not model:
            continue
        limits = {}
        for key, env_key in (
            ("max_upload_mb", f"WATCH_{up}_MAX_UPLOAD_MB"),
            ("chunk_seconds", f"WATCH_{up}_CHUNK_SECONDS"),
            ("min_interval_s", f"WATCH_{up}_MIN_INTERVAL_S"),
        ):
            raw = _env(env_key)
            if raw:
                try:
                    limits[key] = float(raw)
                except ValueError:
                    pass
        BACKENDS[name] = {
            "endpoint": endpoint,
            "model": model,
            "key_env": f"{up}_API_KEY",
            "limits": limits,
        }


def known_backends() -> list[str]:
    _register_extra_providers()
    order = list(DEFAULT_PREFERENCE)
    order += [n for n in BACKENDS if n not in order]
    return order


def _resolve_backend(name: str) -> dict:
    """Merge a preset with any per-field env overrides."""
    _register_extra_providers()
    if name not in BACKENDS:
        raise SystemExit(
            f"Unknown whisper backend: {name} "
            f"(known: {', '.join(known_backends())})"
        )
    spec = dict(BACKENDS[name])
    up = name.upper()
    endpoint = _env(f"WATCH_{up}_ENDPOINT") or spec["endpoint"]
    model = _env(f"WATCH_{up}_MODEL") or spec["model"]
    key_env = _env(f"WATCH_{up}_KEY_ENV") or spec["key_env"]
    limits = dict(spec.get("limits", {}))
    for key, env_key in (
        ("max_upload_mb", f"WATCH_{up}_MAX_UPLOAD_MB"),
        ("chunk_seconds", f"WATCH_{up}_CHUNK_SECONDS"),
        ("min_interval_s", f"WATCH_{up}_MIN_INTERVAL_S"),
    ):
        raw = _env(env_key)
        if raw is not None:
            try:
                limits[key] = float(raw)
            except ValueError:
                pass
    return {
        "name": name,
        "endpoint": endpoint,
        "model": model,
        "key_env": key_env,
        "key_file": _env(f"WATCH_{up}_KEY_FILE"),
        "limits": limits,
    }


def dotenv_paths() -> list[Path]:
    paths = [Path.home() / ".config" / "watch" / ".env"]
    custom = _env("WATCH_ENV_FILE")
    if custom:
        paths.insert(0, Path(custom).expanduser())
    cwd_env = Path.cwd() / ".env"
    if cwd_env not in paths:
        paths.append(cwd_env)
    return paths


def _read_dotenv(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            return value or None
    except OSError:
        return None
    return None


def _read_key_file(path: str) -> str | None:
    """Read a key from a file (0600). Returns None if absent or unreadable.

    Warns to stderr when the file is group/world-readable — a key sitting in a
    world-readable file is a leak waiting to happen.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return None
    try:
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            print(
                f"[watch] WARNING — key file {p} is readable by group/others "
                f"(mode {oct(mode)}). chmod 600 it.",
                file=sys.stderr,
            )
    except OSError:
        pass
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _lookup_key(spec: dict) -> str | None:
    """Resolve a provider key: AUTH=none -> env -> key_file -> dotenv entries.

    WATCH_<NAME>_AUTH=none is for auth-less local servers (Ollama, a LAN
    faster-whisper box): a placeholder key is sent in the header.
    """
    if _env(f"WATCH_{spec['name'].upper()}_AUTH") == "none":
        return "not-needed"
    key_env = spec["key_env"]
    value = _env(key_env)
    if value:
        return value
    if spec.get("key_file"):
        return _read_key_file(spec["key_file"])
    for path in dotenv_paths():
        value = _read_dotenv(path, key_env)
        if value:
            return value
        # a WATCH_<NAME>_KEY_FILE sibling inside the dotenv is honoured too
        ref = _read_dotenv(path, f"WATCH_{spec['name'].upper()}_KEY_FILE")
        if ref:
            value = _read_key_file(ref)
            if value:
                return value
    return None


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend_name, api_key) for the first provider with a key.

    Precedence: explicit `preferred` (the --whisper flag) > $WATCH_WHISPER_BACKEND
    (also readable from the .env files) > first provider with a key.
    """
    if not preferred:
        preferred = _env("WATCH_WHISPER_BACKEND")
    if not preferred:
        for path in dotenv_paths():
            preferred = _read_dotenv(path, "WATCH_WHISPER_BACKEND")
            if preferred:
                break
    order = [preferred] if preferred else known_backends()
    for name in order:
        try:
            spec = _resolve_backend(name)
        except SystemExit:
            if preferred:
                raise
            continue
        value = _lookup_key(spec)
        if value:
            return name, value
    if preferred:  # configured preference is unusable — say so, then fall back
        print(f"[watch] WATCH_WHISPER_BACKEND={preferred} has no usable key — "
              "falling back to first provider with a key", file=sys.stderr)
        for name in known_backends():
            if name == preferred:
                continue
            spec = _resolve_backend(name)
            value = _lookup_key(spec)
            if value:
                return name, value
    return None, None

def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def _audio_duration(path: Path) -> float:
    if shutil.which("ffprobe") is None:
        return 0.0
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return 0.0


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    stdlib-only (urllib has no encoder): each field and the file part get a
    boundary + Content-Disposition, then a closing boundary.
    """
    boundary = uuid.uuid4().hex
    eol = b"\r\n"
    buf = io.BytesIO()
    for key, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{key}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode("utf-8")); buf.write(eol)

    filename = file_path.name
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {ctype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes()); buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0

_LAST_CALL_AT: dict[str, float] = {}


def _pace(name: str, min_interval: float) -> None:
    """Space uploads to a rate-limited box (e.g. a 10 rpm CPU cluster)."""
    if min_interval <= 0:
        return
    last = _LAST_CALL_AT.get(name, 0.0)
    wait = min_interval - (time.monotonic() - last)
    if last > 0 and wait > 0:
        print(f"[watch] {name} whisper rpm guard — waiting {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    _LAST_CALL_AT[name] = time.monotonic()


def _split_audio(audio_path: Path, out_dir: Path, chunk_seconds: float) -> list[tuple[Path, float]]:
    """Slice audio into <=chunk_seconds mp3 pieces. Returns [(path, offset_seconds)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _audio_duration(audio_path)
    if duration <= 0:
        raise SystemExit(f"cannot probe audio duration: {audio_path}")
    n = max(1, int(-(-duration // chunk_seconds)))
    pieces: list[tuple[Path, float]] = []
    for i in range(n):
        offset = i * chunk_seconds
        if offset >= duration:
            break
        piece = out_dir / f"chunk_{i:04d}.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{offset:.3f}", "-t", f"{chunk_seconds:.3f}", "-i", str(audio_path),
             "-c:a", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
             str(piece)],
            check=True, capture_output=True,
        )
        if piece.exists() and piece.stat().st_size > 0:
            pieces.append((piece, offset))
    return pieces


def _post_whisper(
    endpoint: str,
    api_key: str,
    model: str,
    audio_path: Path,
    word_timestamps: bool = False,
    pace_key: str | None = None,
    min_interval: float = 0.0,
) -> dict:
    fields: dict = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    if word_timestamps:
        # OpenAI/Groq accept timestamp_granularities[] as a repeated form field.
        fields["timestamp_granularities[]"] = "word"
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq/NaN sit behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips a WAF rule before auth even runs. Any non-default UA clears it;
        # we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        if pace_key:
            _pace(pace_key, min_interval)
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _post_whisper_backend(spec: dict, api_key: str, audio_path: Path,
                          word_timestamps: bool) -> dict:
    """Transcribe through a resolved backend, honouring its upload limits.

    Small clips go straight through. Anything over the provider's max upload
    size (or a long-audio timeout, when chunk_seconds is set) is split into
    chunk_seconds pieces, each uploaded paced at min_interval_s, with segment/
    word timestamps shifted back onto the original timeline.
    """
    name = spec["name"]
    endpoint, model = spec["endpoint"], spec["model"]
    limits = spec.get("limits", {})
    max_bytes = int(float(limits.get("max_upload_mb", 0)) * 1024 * 1024)
    chunk_seconds = float(limits.get("chunk_seconds", 0) or 0)
    min_interval = float(limits.get("min_interval_s", 0) or 0)

    def direct() -> dict:
        return _post_whisper(
            endpoint, api_key, model, audio_path,
            word_timestamps=word_timestamps, pace_key=name, min_interval=min_interval,
        )

    if max_bytes <= 0 and chunk_seconds <= 0:
        return direct()

    duration = _audio_duration(audio_path)
    too_big = max_bytes > 0 and audio_path.stat().st_size > max_bytes
    too_long = chunk_seconds > 0 and duration > chunk_seconds
    if not too_big and not too_long:
        return direct()

    why = []
    if too_big:
        why.append(f"{audio_path.stat().st_size / 1e6:.0f} MB > {max_bytes / 1e6:.0f} MB upload cap")
    if too_long:
        why.append(f"{duration / 60:.1f} min > {chunk_seconds / 60:.1f} min chunk guidance")
    print(f"[watch] {name} whisper: splitting audio ({', '.join(why)})…", file=sys.stderr)

    pieces = _split_audio(audio_path, audio_path.parent / f"{name}_chunks",
                          chunk_seconds or duration)
    n_parts = len(pieces)
    merged: dict = {"text": "", "segments": [], "words": []}
    for idx, (piece, offset) in enumerate(pieces, 1):
        print(f"[watch] {name} whisper chunk {idx}/{n_parts} (t+{offset / 60:.1f}min)…",
              file=sys.stderr)
        data = _post_whisper(endpoint, api_key, model, piece,
                             word_timestamps=word_timestamps,
                             pace_key=name, min_interval=min_interval)
        chunk_text = (data.get("text") or "").strip()
        if chunk_text:
            merged["text"] = (merged["text"] + " " + chunk_text).strip()
        for seg in data.get("segments") or []:
            seg = dict(seg)
            seg["start"] = round(float(seg.get("start") or 0.0) + offset, 2)
            seg["end"] = round(float(seg.get("end") or 0.0) + offset, 2)
            merged["segments"].append(seg)
        for w in data.get("words") or []:
            w = dict(w)
            w["start"] = round(float(w.get("start") or 0.0) + offset, 3)
            w["end"] = round(float(w.get("end") or 0.0) + offset, 3)
            merged["words"].append(w)
    merged["segments"].sort(key=lambda s: s["start"])
    merged["words"].sort(key=lambda w: w["start"])
    return merged


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred), "
            "OPENAI_API_KEY, NAN_API_KEY, or your own provider (see PROVIDERS.md) "
            f"in the environment or in ~/.config/watch/.env. Run `python3 {setup_py}` to configure."
        )

    spec = _resolve_backend(backend)
    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    size_kb = audio_path.stat().st_size / 1024
    print(f"[watch] audio: {size_kb:.0f} kB — uploading to {backend} Whisper…", file=sys.stderr)

    response = _post_whisper_backend(spec, api_key, audio_path, word_timestamps=False)
    segments = _segments_from_response(response)
    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
    return segments, backend


def transcribe_audio(
    audio_path: Path,
    backend: str | None = None,
    api_key: str | None = None,
    word_timestamps: bool = False,
) -> tuple[list[dict], str, list[dict]]:
    """Upload an existing audio file. Returns (segments, backend, words).

    Words is empty unless word_timestamps=True. Each word is
    {"word": str, "start": float, "end": float}.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key
    if not backend or not api_key:
        raise SystemExit("No Whisper API key available for transcribe_audio()")

    spec = _resolve_backend(backend)
    response = _post_whisper_backend(spec, api_key, audio_path, word_timestamps=word_timestamps)
    segments = _segments_from_response(response)
    words: list[dict] = []
    if word_timestamps:
        for w in response.get("words") or []:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": round(float(w.get("start") or 0.0), 3),
                "end": round(float(w.get("end") or 0.0), 3),
            })
    return segments, backend, words


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: whisper.py <video-path> [<audio-out.mp3>] "
            f"[--backend {'|'.join(known_backends())}]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
