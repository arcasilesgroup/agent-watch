#!/usr/bin/env python3
"""Preflight for /watch on NaN Builders. Portable: stdlib only, no repo deps.

Checks:
  1. NAN_API_KEY resolvable (env, ~/.config/watch/.env, WATCH_NAN_KEY_FILE)
  2. live GET /v1/models — key valid, models the account can call
  3. whisper + qwen3.6 present
  4. watch scripts found and carrying the `nan` backend
     (a pre-fork agent-watch install lacks it; --whisper nan would fail)

Prints one JSON object; exit 0 = ready for `watch.py <source> --whisper nan`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.nan.builders/v1"
WATCH_ROOTS = [
    Path(__file__).resolve().parents[2],          # this repo, when cloned/linked
    Path.home() / ".claude/skills/watch",
    Path.home() / ".agents/skills/watch",
    Path.home() / ".codex/skills/watch",
    Path.home() / ".claude/plugins",              # plugin installs
]


def _dotenv_key(name: str) -> str | None:
    for p in (Path.home() / ".config/watch/.env", Path.cwd() / ".env"):
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().partition("=")
                if k == name and v.strip().strip("\"'"):
                    return v.strip().strip("\"'")
        except OSError:
            pass
    return None


def find_key() -> tuple[str | None, str | None]:
    v = (os.environ.get("NAN_API_KEY") or "").strip()
    if v:
        return v, "env:NAN_API_KEY"
    v = _dotenv_key("NAN_API_KEY")
    if v:
        return v, "~/.config/watch/.env"
    ref = (os.environ.get("WATCH_NAN_KEY_FILE") or _dotenv_key("WATCH_NAN_KEY_FILE") or "").strip()
    if ref:
        try:
            text = Path(ref).expanduser().read_text(encoding="utf-8").strip()
            if text:
                return text, f"key_file:{ref}"
        except OSError:
            pass
    return None, None


def find_watch_scripts() -> dict:
    for root in WATCH_ROOTS:
        if not root.exists():
            continue
        candidates = [root / "scripts"]
        candidates += [p.parent for p in root.rglob("watch.py") if "scripts" in p.parts]
        for cand in candidates:
            if not (cand / "watch.py").exists():
                continue
            try:
                has_nan = "--whisper" in (cand / "watch.py").read_text(encoding="utf-8") and \
                    "nan" in (cand / "whisper.py").read_text(encoding="utf-8")
            except OSError:
                has_nan = False
            return {"dir": str(cand), "watch": True,
                    "whisper": (cand / "whisper.py").exists(), "nan_backend": has_nan}
    return {"dir": None, "watch": False, "whisper": False, "nan_backend": False}


def main() -> int:
    key, source = find_key()
    out: dict = {"key_source": source}
    if key:
        out["key"] = f"present ({len(key)} chars)"

    models: list[str] = []
    if key:
        req = urllib.request.Request(
            f"{BASE}/models",
            headers={"Authorization": f"Bearer {key}",
                     "User-Agent": "watch-nan-skill/1.0 (preflight)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                models = sorted(m["id"] for m in json.load(r)["data"])
        except urllib.error.HTTPError as exc:
            out["error"] = f"GET /v1/models -> HTTP {exc.code}"
            if exc.code in (401, 403):
                out["fix"] = "key invalid/expired — regenerate at https://cloud.nan.builders/"
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 2
        except OSError as exc:
            out["error"] = f"GET /v1/models -> {exc}"
            out["fix"] = "network/VPN/DNS issue before anything else"
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 2

    out["models"] = models
    out["whisper_available"] = "whisper" in models
    out["qwen36_available"] = "qwen3.6" in models
    out["watch_scripts"] = find_watch_scripts()

    problems = []
    if not key:
        problems.append("no NAN_API_KEY (env, ~/.config/watch/.env, or WATCH_NAN_KEY_FILE)"
                        " — get one at https://cloud.nan.builders/ (settings -> API Keys)")
    if key and not out["whisper_available"]:
        problems.append("key works but `whisper` is not in its model list (membership tier?)")
    if not out["watch_scripts"]["watch"]:
        problems.append("watch scripts not found — clone this repo or install the plugin")
    elif not out["watch_scripts"]["nan_backend"]:
        problems.append("installed watch scripts predate the nan backend — update them")

    out["ready"] = not problems
    if problems:
        out["fix"] = problems
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
