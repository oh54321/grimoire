#!/usr/bin/env bash
# Grimoire installer — installs the `grimoire` MCP server.
# Prefers an isolated install (pipx, then uv tool); falls back to a dedicated venv.
#
# CPU-only install (skips the multi-GB NVIDIA/CUDA wheels that torch pulls by
# default — useless on a machine without an NVIDIA GPU):
#     GRIMOIRE_CPU=1 ./install.sh        # or:  ./install.sh --cpu
# This routes through a dedicated venv and pre-installs CPU torch from the
# PyTorch index, since pipx/uv can't reliably force a per-package index.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cpu_only="${GRIMOIRE_CPU:-0}"
for arg in "$@"; do [ "$arg" = "--cpu" ] && cpu_only=1; done

if ! command -v python3 >/dev/null 2>&1 || \
   ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
  echo "Grimoire needs Python 3.11+." >&2
  exit 1
fi

run_cmd="grimoire"
if [ "$cpu_only" = "1" ]; then
  echo "→ CPU-only install (no CUDA): dedicated venv at ~/.grimoire/venv…"
  python3 -m venv "$HOME/.grimoire/venv"
  "$HOME/.grimoire/venv/bin/pip" install --quiet --upgrade pip
  # Pin torch to the CPU build first so the project install doesn't pull CUDA wheels.
  "$HOME/.grimoire/venv/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu
  "$HOME/.grimoire/venv/bin/pip" install "$here"
  run_cmd="$HOME/.grimoire/venv/bin/grimoire"
elif command -v pipx >/dev/null 2>&1; then
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

# --- Claude Code prompt-reminder hook (keyword-gated, low-token) -------------
# Installs a UserPromptSubmit hook that nudges the model toward the Grimoire
# workflow, but only when the prompt is Grimoire-related (zero tokens otherwise).
hooks_dir="$HOME/.grimoire/hooks"
mkdir -p "$hooks_dir"
cp "$here/hooks/grimoire_prompt_reminder.py" "$hooks_dir/grimoire_prompt_reminder.py"
chmod +x "$hooks_dir/grimoire_prompt_reminder.py"

settings="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"
if python3 - "$settings" "$hooks_dir/grimoire_prompt_reminder.py" <<'PY'
import json, sys
settings_path, script = sys.argv[1], sys.argv[2]
command = f'python3 "{script}"'
try:
    with open(settings_path) as fh:
        data = json.load(fh)
except FileNotFoundError:
    data = {}
except ValueError:
    sys.exit("existing settings.json is not valid JSON; leaving it untouched")
if not isinstance(data, dict):
    sys.exit("existing settings.json is not a JSON object; leaving it untouched")

entries = data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
def is_ours(entry):
    return any("grimoire_prompt_reminder" in h.get("command", "")
               for h in entry.get("hooks", []))
# Idempotent: drop any prior Grimoire reminder entry before re-adding.
entries[:] = [e for e in entries if not is_ours(e)]
entries.append({"hooks": [{"type": "command", "command": command}]})

with open(settings_path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
then
  echo "✅ Registered prompt-reminder hook in $settings"
else
  echo "⚠  Skipped hook registration for $settings (see message above)"
fi

echo
echo "Register with Claude Code (absolute path + user scope = robust across shells/projects):"
echo "     claude mcp add grimoire --scope user -- $run_cmd"
echo
echo "(The prompt-reminder hook activates in new Claude Code sessions; in an open"
echo " session, open /hooks once or restart to load it.)"
