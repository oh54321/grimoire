#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook for the Grimoire MCP.

Injects a short reminder of the Grimoire tool workflow, but ONLY when the user's
prompt looks Grimoire-related. Unrelated prompts produce no output, so the hook
costs zero extra tokens on turns that have nothing to do with the library.

Wired up by install.sh into ~/.claude/settings.json. Reads the hook payload as
JSON on stdin; emits a UserPromptSubmit additionalContext block on stdout.
"""
from __future__ import annotations

import json
import re
import sys

REMINDER = (
    "Grimoire MCP reminder — discover/search BEFORE define (reuse any match as a "
    "dependency). Code enters the library only via define -> implement (needs "
    "passing tests). To ingest a repo/MCP, use fetch_source -> survey_source -> "
    "read_source -> define/implement -> discard_source (not an external file-fetch "
    "tool). run_scratch: pass deps=[node_ids] and call nodes by their plain name — "
    "never `import grimoire`. hide/unhide toggle searchability (not display; use "
    "view/read_code to inspect); mark_tool/mark_helper toggle is_tool."
)

# Distinctive, low-false-positive triggers: the server name plus tool names that
# are unlikely to appear in unrelated prompts. Deliberately excludes generic words
# like "define"/"implement"/"library" to keep unrelated turns silent.
KEYWORDS = re.compile(
    r"\b(grimoire|run_scratch|fetch_source|survey_source|read_source|"
    r"discard_source|mark_tool|mark_helper|reusable[- ]code)\b",
    re.IGNORECASE,
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    prompt = str(payload.get("prompt", ""))
    if not KEYWORDS.search(prompt):
        return  # unrelated prompt — inject nothing
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": REMINDER,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
