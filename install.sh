#!/usr/bin/env bash
# One-line bootstrap for agent-watch:
#
#   curl -fsSL https://raw.githubusercontent.com/arcasilesgroup/agent-watch/main/install.sh | bash
#
# Clones (or refreshes) the repo at ~/.agents/watch-src, then links the `watch`
# and `ai-nan-watch` skills into every agent skills surface present on the machine.
# Safe to re-run; keeps any surface-managed copy you replaced yourself.
set -euo pipefail

REPO_URL="${AGENT_WATCH_REPO:-https://github.com/arcasilesgroup/agent-watch.git}"
SRC="${AGENT_WATCH_SRC:-$HOME/.agents/watch-src}"

if command -v git >/dev/null 2>&1; then
  if [[ -d "$SRC/.git" ]]; then
    git -C "$SRC" pull --ff-only -q || echo "note: pull failed (offline? local edits?) — continuing with what's on disk"
  else
    mkdir -p "$(dirname "$SRC")"
    git clone --depth 1 -q "$REPO_URL" "$SRC"
  fi
  bash "$SRC/scripts/install-skill.sh"
else
  echo "git is required to install agent-watch (or copy the repo manually and run scripts/install-skill.sh)." >&2
  exit 1
fi
