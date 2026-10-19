"""Claude Code hooks, server side. hooks/*.sh pipe the hook's stdin JSON to POST /v1/hook/<event> and print the reply,
so the scripts need only curl (no jq) and the transcript parsing is Python, tested here.

Hook input (Claude Code hooks reference): session_id, transcript_path, cwd, hook_event_name; UserPromptSubmit adds
prompt, PreCompact adds trigger and custom_instructions, Stop adds stop_hook_active.
TODO(confirm against the current Claude Code docs before shipping the wizard):
- UserPromptSubmit injects hookSpecificOutput.additionalContext, and the size it keeps (we send at most CAP);
- the transcript line shape read in turns(): {"type": "user"|"assistant", "message": {"content": str | blocks},
  "isMeta", "isSidechain", "isCompactSummary"}; whether injected context is ever written into a user line (OURS
  strips our own blocks either way, so memory never re-ingests what it recalled).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bench.adapters.spookling.recall import ABSTAIN

EVENTS = ("user-prompt-submit", "pre-compact", "stop")
CAP = 50_000  # characters of injected context: about 12,500 tokens at 4 characters a token
OURS = re.compile(r"<(invariant|recalled-data|memory-index|kept-before-compaction)\b.*?</\1>", re.S)
COMMAND = ("<command-", "<local-command-")  # slash-command wrappers and their output: not conversation


def turns(path: str | Path) -> list[dict]:
    """The conversation in a Claude Code transcript: user prompts and assistant text, consecutive parts of one role
    merged (tool calls and results are left out)."""
    out: list[dict] = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") not in ("user", "assistant") \
                or e.get("isMeta") or e.get("isSidechain") or e.get("isCompactSummary"):
            continue
        c = (e.get("message") or {}).get("content")
        text = c if isinstance(c, str) else "\n".join(
            b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
        text = OURS.sub("", text).strip()
        if not text or text.startswith(COMMAND):
            continue
        if out and out[-1]["role"] == e["type"]:
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": e["type"], "content": text})
    return out


def handle(store, event: str, p: dict) -> dict:
    """What the hook prints. UserPromptSubmit: the recalled block as additional context. Stop: log the turns not yet
    logged. PreCompact: the same, then keep the session's rules, decisions and values before the summary drops them."""
    sid = str(p.get("session_id") or "claude-code")
    if event == "user-prompt-submit":
        prompt = p.get("prompt") or ""
        ctx = store.retrieve(prompt)["context"] if prompt.strip() else ""
        if not ctx or ctx == ABSTAIN:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx[:CAP]}}
    path = str(p.get("transcript_path") or "")
    if path.endswith(".jsonl") and Path(path).is_file():
        conversation = turns(path)
        if conversation:
            store.ingest(sid, conversation, agent="claude-code", tail=True)
    if event == "pre-compact":
        store.compact(sid)
    return {}
