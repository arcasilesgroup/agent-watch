# Authors & credits

## Original project

**Bradley Bonanno** — [bradautomates/claude-video](https://github.com/bradautomates/claude-video)

The original `/watch` skill: yt-dlp download, the ffmpeg frame pipeline, the Groq/OpenAI
Whisper backends, the multi-surface install flow, and the SessionStart hook. Released under
the MIT license, © 2026 Bradley Bonanno.

## Upstream distribution — claude-watch

**Taoufik** — [taoufik123-collab/claude-watch](https://github.com/taoufik123-collab/claude-watch)

Scene-change frame extraction, the 0-10s hook microscope, the structured `report.md`, and
optional Obsidian auto-save, on top of Bradley's pipeline.

## This distribution — agent-watch

**Arcasiles Group** — [github.com/arcasilesgroup](https://github.com/arcasilesgroup)

The bring-your-own-provider Whisper layer (any OpenAI-compatible endpoint), the NaN
Builders cluster backend with automatic 25 MB / 10 rpm / long-audio chunking, the
`skills/ai-nan-watch` provider-adapter skill, the one-line cross-surface installer (`.agents`,
Claude, Codex, pi, OpenCode), the ffmpeg-9 `-fps_mode` fix, and tool-agnostic naming.
agent-watch is a single-commit continuation of claude-watch, MIT, upstream history
preserved in the original repository.
