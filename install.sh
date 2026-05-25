#!/usr/bin/env bash
# Grimoire installer — installs the `grimoire` MCP server.
# Prefers an isolated install (pipx, then uv tool); falls back to a dedicated venv.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1 || \
   ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
  echo "Grimoire needs Python 3.11+." >&2
  exit 1
fi

run_cmd="grimoire"
if command -v pipx >/dev/null 2>&1; then
  echo "→ Installing with pipx (isolated)…"
  pipx install "$here"
elif command -v uv >/dev/null 2>&1; then
  echo "→ Installing with uv tool (isolated)…"
  uv tool install "$here"
else
  echo "→ pipx/uv not found; installing into a dedicated venv at ~/.grimoire/venv…"
  python3 -m venv "$HOME/.grimoire/venv"
  "$HOME/.grimoire/venv/bin/pip" install --quiet --upgrade pip
  "$HOME/.grimoire/venv/bin/pip" install "$here"
  run_cmd="$HOME/.grimoire/venv/bin/grimoire"
fi

# Resolve to an absolute path so the MCP registration never depends on PATH.
if [ "$run_cmd" = "grimoire" ]; then
  run_cmd="$(command -v grimoire 2>/dev/null || echo "$HOME/.local/bin/grimoire")"
fi

echo
echo "✅ Installed at: $run_cmd"
echo
echo "Register with Claude Code (absolute path + user scope = robust across shells/projects):"
echo "     claude mcp add grimoire --scope user -- $run_cmd"
