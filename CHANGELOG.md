# Changelog

All notable changes to `/watch` (agent-watch) are documented here.
Earlier history lives upstream: [taoufik123-collab/claude-watch](https://github.com/taoufik123-collab/claude-watch/blob/main/CHANGELOG.md)
and [bradautomates/claude-video](https://github.com/bradautomates/claude-video).

## [0.3.2] — 2026-08-29 — provider-adapter naming

### Changed
- `skills/ai-watch` renamed to **`skills/ai-nan-watch`**: one adapter skill per provider (`watch` stays the provider-agnostic core). Switching providers later = add a sibling `ai-<provider>-watch`, not rename this one. Installer retires legacy `nan-watch` / `ai-watch` symlinks on re-run.

## [0.3.1] — 2026-08-29 — rename companion skill

### Changed
- `skills/nan-watch` renamed to **`skills/ai-watch`** — the skill is provider-agnostic by name (NaN is just the current reference provider); description and installer links follow.

## [0.3.0] — 2026-08-29 — agent-watch initial public release

Single-commit continuation of claude-watch 0.2.0 (scene-change frames, hook
microscope, structured report, Obsidian ingest — all unchanged and working),
rebranded tool-agnostic and extended with:

### Added
- **Bring-your-own-provider Whisper layer** in `scripts/whisper.py`: every backend (endpoint, model id, key env, key file, upload caps) is env-overridable, and custom OpenAI-compatible providers register via `WATCH_EXTRA_PROVIDERS` (Ollama, faster-whisper, vLLM, company gateways — see PROVIDERS.md). `--whisper <backend>` accepts any known name. Key files warn on loose permissions.
- **NaN Builders Whisper backend** (`--whisper nan`, `NAN_API_KEY`): whisper-large-v3 on the community cluster (`api.nan.builders`), free with membership. The client honours its documented constraints: 10 rpm pacing, 25 MB upload cap, and long-audio auto-chunking into ≤110 s pieces with timestamps re-aligned to the original timeline.
- **`skills/nan-watch`** — companion skill teaching any agent how to combine /watch with the NaN cluster (rate limits, frame token budgets, multi-hour video strategy) plus a live `preflight.py`.
- **One-line install for every agent surface**: `install.sh` (curl bootstrap) links `watch` + `nan-watch` into `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.pi/agent/skills`, `~/.config/opencode/skills`.
- `WATCH_WHISPER_BACKEND` env/.env preference so a configured machine never has to pass `--whisper` again.
- `scripts/setup.py --provider <name>` — one-shot provider configuration: writes the key and preference to `~/.config/watch/.env`, validates the key live for preset providers, prints one result line.

### Fixed
- `frames.py` scene-change extraction crashed on ffmpeg ≥ 9 (`-vsync` removed). Now probes for `-fps_mode` support and falls back to `-vsync` on older ffmpeg (4/5-era).
- Report markers renamed `<!-- pending agent fill: ... -->` — the contract is agent-agnostic now.
