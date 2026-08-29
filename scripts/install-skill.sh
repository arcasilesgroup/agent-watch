#!/usr/bin/env bash
# Install this repo's skills into every agent surface found on the machine.
#
#   watch          -> <surface>/skills/watch           (this repo root: SKILL.md + scripts/)
#   ai-nan-watch   -> <surface>/skills/ai-nan-watch    (per-provider adapter, currently NaN)
#
# Surfaces probed (standard Agent Skills locations — Claude Code, omp, pi,
# OpenCode, Codex all read one or more): ~/.agents/skills, ~/.claude/skills,
# ~/.codex/skills, ~/.pi/agent/skills, ~/.config/opencode/skills
# Idempotent: re-running refreshes symlinks. Pass --copy for real directories
# instead of symlinks (e.g. if the clone lives on a volume symlinks can't cross).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=link
[[ "${1:-}" == "--copy" ]] && MODE=copy

install_one() {
  local src="$1" dest="$2"
  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ ! -L "$dest" ]]; then
      echo "  keep: $dest exists and is not a symlink (yours to manage)"
      return
    fi
    rm "$dest"
  fi
  if [[ "$MODE" == copy ]]; then
    mkdir -p "$dest"
    cp -R "$src/." "$dest/"
  else
    ln -s "$src" "$dest"
  fi
  echo "  $dest <- $src"
}

touch_surface() {
  local root="$1"
  echo "[$root]"
  mkdir -p "$root"
  install_one "$REPO" "$root/watch"
  install_one "$REPO/skills/ai-nan-watch" "$root/ai-nan-watch"
  # retire symlinks this repo used to install under (provider renames)
  for legacy in nan-watch ai-watch; do
    [[ -L "$root/$legacy" ]] && { rm "$root/$legacy"; echo "  rm: $root/$legacy (legacy)"; } || true
  done
  return 0
}

found=0
for surface in "$HOME/.agents/skills" "$HOME/.claude/skills" "$HOME/.codex/skills" \
               "$HOME/.pi/agent/skills" "$HOME/.config/opencode/skills"; do
  parent="$(dirname "$surface")"
  [[ -d "$parent" ]] || continue
  found=1
  touch_surface "$surface"
done

if [[ "$found" == 0 ]]; then
  echo "no agent surfaces found (~/.agents, ~/.claude, ~/.codex, ~/.pi, opencode)."
  echo "create one and re-run, or clone directly:"
  echo "  git clone $REPO  ~/.agents/skills/watch"
  exit 1
fi

echo "done. Restart your agent(s) to pick up the skills."
