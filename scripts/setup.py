#!/usr/bin/env python3
"""Setup / preflight for /watch.

Modes:
  setup.py --check      Silent preflight. Exit 0 if ready, 2/3/4 on failure.
  setup.py --json       Machine-readable status for an agent to parse.
  setup.py              Installer. Auto-installs deps, scaffolds .env, marks SETUP_COMPLETE.

Design:
- Silent on success: --check exits 0 with no output when everything's ready so
  that /watch doesn't spam "setup is complete" on every turn.
- Idempotent: re-running the installer is safe — it never clobbers existing
  keys and only appends missing ones.
- SETUP_COMPLETE=true in ~/.config/watch/.env tells us the user has been
  through a successful installer run at least once.
- Never sudo. On macOS, auto-install via brew. Elsewhere, print exact commands.
- Never write an API key to disk automatically — only scaffold placeholders.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp"]
CONFIG_DIR = Path.home() / ".config" / "watch"
CONFIG_FILE = CONFIG_DIR / ".env"
ENV_TEMPLATE = """# /watch API configuration
#
# Whisper transcription fallback — used only when yt-dlp cannot get captions
# (or when you point /watch at a local file with no subtitles).
#
# Any OpenAI-compatible transcription endpoint works. Built-in presets:
#   Groq    (whisper-large-v3) — preferred: cheap, fast. https://console.groq.com/keys
#   OpenAI  (whisper-1)        — https://platform.openai.com/api-keys
#   NaN     (whisper=large-v3) — community cluster, 10 rpm / 25 MB caps
#                                (long audio is auto-chunked). https://cloud.nan.builders/
# The first key set below wins; force one with `--whisper <name>`.
#
# Bring your own provider (Ollama, faster-whisper, vLLM, ElevenLabs, a company
# gateway…): uncomment WATCH_EXTRA_PROVIDERS and the WATCH_<NAME>_* lines, then
# use `--whisper <name>`. See PROVIDERS.md for the full reference.
#
# Leave all blank to disable Whisper — /watch still works, videos without
# native captions just come back frames-only.

GROQ_API_KEY=
OPENAI_API_KEY=
NAN_API_KEY=

# --- custom providers (advanced) ---
# WATCH_NAN_KEY_FILE=              # read the key from this file instead (chmod 600)
# WATCH_EXTRA_PROVIDERS=mywhisper
# WATCH_MYWHISPER_ENDPOINT=http://127.0.0.1:8000/v1/audio/transcriptions
# WATCH_MYWHISPER_MODEL=faster-whisper-large-v3
# MYWHISPER_API_KEY=               # or WATCH_MYWHISPER_AUTH=none for auth-less local servers
"""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _check_binaries() -> list[str]:
    return [b for b in REQUIRED_BINARIES if not _which(b)]


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr if a secrets file is world/group readable."""
    try:
        mode = path.stat().st_mode
        if mode & 0o044:
            sys.stderr.write(
                f"[watch] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError:
        pass


def _read_env_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if not CONFIG_FILE.exists():
        return None
    _check_file_permissions(CONFIG_FILE)
    try:
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() != name:
                continue
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                raw = raw[1:-1]
            return raw or None
    except OSError:
        return None
    return None


def _have_api_key() -> tuple[bool, str | None]:
    """Shared resolution with the whisper client: env + .env + KEY_FILE + extras."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from whisper import load_api_key  # noqa: PLC0415
        backend, key = load_api_key()
        return (True, backend) if backend and key else (False, None)
    except Exception:
        # fall back to the simple env/dotenv check if the client import breaks
        for env_name, backend in (
            ("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"), ("NAN_API_KEY", "nan"),
        ):
            if _read_env_key(env_name):
                return True, backend
        return False, None


def is_first_run() -> bool:
    """True if the installer hasn't completed successfully yet."""
    return _read_env_key("SETUP_COMPLETE") != "true"


def _scaffold_env() -> bool:
    """Create ~/.config/watch/.env with placeholders if missing."""
    if CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(ENV_TEMPLATE, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    return True


def _write_setup_complete() -> None:
    """Idempotently append SETUP_COMPLETE=true to .env.

    Used only after a fully successful install (deps + key). Future sessions
    detect this marker to skip wizard-style UI and stay silent.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = ""
    if CONFIG_FILE.exists():
        existing = CONFIG_FILE.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.strip().startswith("SETUP_COMPLETE="):
                return
        if existing and not existing.endswith("\n"):
            existing += "\n"
        CONFIG_FILE.write_text(existing + "SETUP_COMPLETE=true\n", encoding="utf-8")
    else:
        CONFIG_FILE.write_text(ENV_TEMPLATE + "\nSETUP_COMPLETE=true\n", encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def _brew_pkg(missing: list[str]) -> list[str]:
    pkgs: list[str] = []
    for bin_name in missing:
        if bin_name in ("ffmpeg", "ffprobe"):
            if "ffmpeg" not in pkgs:
                pkgs.append("ffmpeg")
        elif bin_name == "yt-dlp":
            if "yt-dlp" not in pkgs:
                pkgs.append("yt-dlp")
        else:
            pkgs.append(bin_name)
    return pkgs


def _install_macos(missing: list[str]) -> tuple[bool, str]:
    if _which("brew") is None:
        return False, (
            "Homebrew is not installed. Install it from https://brew.sh, then re-run setup. "
            "Or install manually: `brew install " + " ".join(_brew_pkg(missing)) + "`"
        )
    pkgs = _brew_pkg(missing)
    if not pkgs:
        return True, "nothing to install"
    cmd = ["brew", "install", *pkgs]
    print(f"[setup] running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return False, f"brew install failed with exit code {result.returncode}"
    return True, f"installed via brew: {', '.join(pkgs)}"


def _install_hint_linux(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("apt: `sudo apt install ffmpeg` or dnf: `sudo dnf install ffmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("`pipx install yt-dlp` (recommended) or `pip install --user yt-dlp`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _install_hint_windows(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("winget: `winget install Gyan.FFmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("winget: `winget install yt-dlp.yt-dlp` or pip: `pip install --user yt-dlp`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _status() -> dict:
    """Structured preflight snapshot."""
    missing = _check_binaries()
    has_key, backend = _have_api_key()

    if not missing and has_key:
        status = "ready"
    elif missing and not has_key:
        status = "needs_install_and_key"
    elif missing:
        status = "needs_install"
    else:
        status = "needs_key"

    return {
        "status": status,
        "first_run": is_first_run(),
        "missing_binaries": missing,
        "whisper_backend": backend,
        "has_api_key": has_key,
        "config_file": str(CONFIG_FILE),
        "platform": platform.system(),
    }


def cmd_check() -> int:
    """Silent-on-success preflight.

    Exit 0 with no output when ready. On failure, print one actionable line
    to stderr and return:
      2 → binaries missing
      3 → API key missing
      4 → both missing
    """
    s = _status()
    if s["status"] == "ready":
        return 0

    parts = []
    if s["missing_binaries"]:
        parts.append(f"missing binaries: {', '.join(s['missing_binaries'])}")
    if not s["has_api_key"]:
        parts.append("no Whisper API key (GROQ_API_KEY, OPENAI_API_KEY, or NAN_API_KEY)")
    installer = Path(__file__).resolve()
    sys.stderr.write(
        f"[watch] setup incomplete ({'; '.join(parts)}). "
        f"Run: python3 {installer}\n"
    )
    sys.stderr.flush()

    if s["missing_binaries"] and not s["has_api_key"]:
        return 4
    if s["missing_binaries"]:
        return 2
    return 3


def cmd_json() -> int:
    json.dump(_status(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_install() -> int:
    missing = _check_binaries()
    installed_deps = False
    if missing:
        system = platform.system()
        if system == "Darwin":
            ok, msg = _install_macos(missing)
            print(f"[setup] {msg}", file=sys.stderr)
            if not ok:
                return 2
            still_missing = _check_binaries()
            if still_missing:
                print(f"[setup] still missing after install: {', '.join(still_missing)}", file=sys.stderr)
                return 2
            installed_deps = True
        elif system == "Linux":
            print("[setup] dependencies missing on Linux — please install:", file=sys.stderr)
            print("  " + _install_hint_linux(missing), file=sys.stderr)
            return 2
        elif system == "Windows":
            print("[setup] dependencies missing on Windows — please install:", file=sys.stderr)
            print("  " + _install_hint_windows(missing), file=sys.stderr)
            return 2
        else:
            print(f"[setup] unsupported platform ({system}) for auto-install. Install manually:", file=sys.stderr)
            print(f"  missing: {', '.join(missing)}", file=sys.stderr)
            return 2

    created = _scaffold_env()
    if created:
        print(f"[setup] created config: {CONFIG_FILE}")
    else:
        print(f"[setup] config exists: {CONFIG_FILE}")

    has_key, backend = _have_api_key()
    if has_key:
        _write_setup_complete()
        print(f"[setup] ready. whisper backend: {backend}")
        if installed_deps:
            print("[setup] installed dependencies; /watch is fully set up.")
        return 0

    print("")
    print("[setup] one step left: add a Whisper API key.")
    print("")
    print(f"  Edit {CONFIG_FILE} and set either:")
    print("    GROQ_API_KEY=...    (preferred — cheaper, faster; get one at console.groq.com/keys)")
    print("    OPENAI_API_KEY=...  (fallback; get one at platform.openai.com/api-keys)")
    print("")
    print("  Without a key, /watch still works but videos without captions come back frames-only.")
    return 3


def _set_env_keys(updates: dict[str, str]) -> None:
    """Set/unset keys in ~/.config/watch/.env, preserving comments and order."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines() if CONFIG_FILE.exists() else []
    done = set()
    out: list[str] = []
    for line in lines:
        k, sep, _ = line.partition("=")
        key = k.strip()
        if sep and key in updates:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in done:
            out.append(f"{key}={value}")
    CONFIG_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def cmd_provider(argv: list[str]) -> int:
    """One-shot: `setup.py --provider <name> [--key <k>] [--key-file <path>]`.

    Writes the provider key + WATCH_WHISPER_BACKEND=<name> to
    ~/.config/watch/.env, validates the key live via the provider's
    /v1/models-style endpoint, and never prints the key. Key source order:
    --key flag > --key-file > $<NAME>_API_KEY > interactive prompt.
    """
    import getpass
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import whisper  # noqa: PLC0415

    if not argv:
        print("usage: setup.py --provider <name> [--key <k> | --key-file <path>]", file=sys.stderr)
        print(f"known providers: {', '.join(whisper.known_backends())} "
              "(custom ones via WATCH_EXTRA_PROVIDERS — see PROVIDERS.md)", file=sys.stderr)
        return 2
    name = argv[0]
    key: str | None = None
    if "--key" in argv:
        key = argv[argv.index("--key") + 1]
    elif "--key-file" in argv:
        key = whisper._read_key_file(argv[argv.index("--key-file") + 1])
    try:
        spec = whisper._resolve_backend(name)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not key:
        key = os.environ.get(spec["key_env"], "").strip() or None
    if not key and whisper._env(f"WATCH_{name.upper()}_AUTH") == "none":
        key = "not-needed"
    if not key and sys.stdin.isatty():
        key = getpass.getpass(f"{spec['key_env']} for '{name}' (input hidden): ").strip() or None
    if not key:
        print(f"no key supplied: pass --key / --key-file, set {spec['key_env']}, "
              "or run interactively", file=sys.stderr)
        return 2

    # live validation: derive a models URL from the transcription endpoint
    models_url = spec["endpoint"].replace("/audio/transcriptions", "/models")
    verified = False
    if models_url != spec["endpoint"]:
        try:
            import urllib.request  # noqa: PLC0415
            req = urllib.request.Request(
                models_url,
                headers={"Authorization": f"Bearer {key}",
                         "User-Agent": "agent-watch-setup/1.0"})
            import json as _json  # noqa: PLC0415
            with urllib.request.urlopen(req, timeout=15) as resp:
                ids = {m.get("id") for m in _json.load(resp)["data"]}
            if spec["model"] in ids or not ids:
                verified = True
                print(f"[setup] key verified — provider '{name}' lists model "
                      f"'{spec['model']}'")
            else:
                print(f"[setup] WARNING: key works but '{spec['model']}' is not in "
                      f"its model list ({', '.join(sorted(ids))})")
        except Exception as exc:  # network or bad key — informative, not fatal
            print(f"[setup] WARNING: could not verify key live: {type(exc).__name__}: {exc}")
    else:
        print("[setup] custom endpoint without a /models sibling — skipping live check")

    _set_env_keys({spec["key_env"]: key, "WATCH_WHISPER_BACKEND": name,
                   "SETUP_COMPLETE": "true"})
    print(f"[setup] configured '{name}' in {CONFIG_FILE} (mode 0600). "
          "/watch will use it automatically; override per-run with --whisper <name>.")
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--check":
            return cmd_check()
        if arg == "--json":
            return cmd_json()
        if arg == "--provider":
            return cmd_provider(sys.argv[2:])
    return cmd_install()

if __name__ == "__main__":
    raise SystemExit(main())
