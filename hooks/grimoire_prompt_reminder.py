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
    "Grimoire MCP — this looks like reusable-code work, so the library may already "
    "have what you need or should own what you build. BEFORE writing or editing any "
    "code, FIRST call discover/search to look for a node to reuse (reuse any match "
    "as a dependency). New code enters the library ONLY via define -> implement "
    "(must pass a pytest gate). To pull in a repo or MCP server, use the `ingest` "
    "prompt: fetch_source -> survey_source -> read_source -> define/implement -> "
    "discard_source (never a plain file-fetch). Use run_scratch for throwaway macros "
    "(pass deps=[node_ids], call nodes by their plain name; never `import grimoire`). "
    "If you decide to write code OUTSIDE the library, state explicitly why Grimoire "
    "does not apply before doing so."
)

# Triggers fall in two bands:
#  - High-precision Grimoire-specific names (server + tool names) that are unlikely
#    to appear in unrelated prompts.
#  - Broader reuse/library/ingest intent signals: prompts about building reusable
#    code, growing a tool/code library, or ingesting a repo are Grimoire-relevant
#    even when they never say "grimoire". Still excludes bare generic words like
#    "define"/"implement"/"function" to avoid firing on ordinary coding turns.
KEYWORDS = re.compile(
    r"\b(?:grimoire|run_scratch|fetch_source|survey_source|read_source|"
    r"discard_source|mark_tool|mark_helper|"
    r"reusable|reuse|ingest|"
    r"tool\s+codebase|code\s+library|personal\s+library)\b",
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
