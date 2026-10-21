"""Claude Code hooks, server side. hooks/*.js (node, any OS) and hooks/*.sh (curl) pipe the hook's stdin JSON to
POST /v1/hook/<event> and print the reply, so the transcript parsing is Python, tested here.
Checked against the Claude Code hooks reference on 21 Oct 2026 (docs/claude-code-facts.md):

- Input: session_id, transcript_path, cwd, hook_event_name; UserPromptSubmit adds prompt, PreCompact adds trigger and
  custom_instructions, Stop adds stop_hook_active and last_assistant_message.
- UserPromptSubmit output: hookSpecificOutput.additionalContext, at most 10,000 characters. A longer value is saved
  to a file and Claude gets only the path and a 2,000-character preview, so we cut at CAP.
- The transcript may lag at Stop; last_assistant_message is the final reply, so Stop adds it when the file has not.
- The transcript line format is internal to Claude Code and changes between versions (sessions docs). turns() reads
  what 2.1.283 writes: {"type": "user"|"assistant", "message": {"content": str | blocks}, "isMeta",
  "isCompactSummary"}; injected context is stored as "attachment" lines, which turns() skips. OURS strips our own
  blocks anyway, so memory never re-ingests what it recalled.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from bench.adapters.spookling.recall import ABSTAIN

from .store import jsonl

EVENTS = ("user-prompt-submit", "pre-compact", "stop")
CAP = 10_000  # Claude Code's cap on additionalContext, in characters (counted as UTF-16 units, the stricter count)
OURS = re.compile(r"<(invariant|recalled-data|memory-index|kept-before-compaction)\b.*?</\1>", re.S)
COMMAND = ("<command-", "<local-command-")  # slash-command wrappers and their output: not conversation
MNT = Path("/mnt")  # WSL's default automount root ([automount] root in /etc/wsl.conf moves it)


def cap(text: str, n: int = CAP) -> str:
    """At most n UTF-16 units (an emoji counts two, as in JavaScript), never half a character."""
    return text.encode("utf-16-le")[: 2 * n].decode("utf-16-le", "ignore")


def local_path(p: str) -> Path:
    """transcript_path as this machine sees it. A Windows Claude Code talking to a server in WSL sends C:\\...; WSL
    mounts that drive at /mnt/c. ponytail: MNT is the default root; read /etc/wsl.conf if someone moves it."""
    m = re.match(r"([A-Za-z]):[\\/](.*)", p)
    if m and os.name != "nt" and (MNT / m[1].lower()).is_dir():
        return MNT.joinpath(m[1].lower(), *[x for x in re.split(r"[\\/]", m[2]) if x])
    return Path(p)


def turns(path: str | Path) -> list[dict]:
    """The conversation in a Claude Code transcript: user prompts and assistant text, consecutive parts of one role
    merged (tool calls and results are left out)."""
    out: list[dict] = []
    for line in jsonl(Path(path).read_text(encoding="utf-8", errors="replace")):
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
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": cap(ctx)}}
    path = local_path(str(p.get("transcript_path") or ""))
    if path.suffix == ".jsonl" and path.is_file():
        conversation = turns(path)
        last = OURS.sub("", str(p.get("last_assistant_message") or "")).strip()
        if conversation and last and event == "stop":  # the file can lag the final reply at Stop
            if conversation[-1]["role"] != "assistant":
                conversation.append({"role": "assistant", "content": last})
            elif last not in conversation[-1]["content"]:
                conversation[-1]["content"] += "\n\n" + last
        if conversation:
            store.ingest(sid, conversation, agent="claude-code", tail=True)
    if event == "pre-compact":
        store.compact(sid)
    return {}
