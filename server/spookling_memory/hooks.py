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
- A developer's transcript is mostly not conversation: background-task reports and monitor events arrive as user
  messages (<task-notification>), pastes as <pasted_content>, and the assistant narrates between tool calls. clean()
  keeps the user's own words and the assistant's final prose (rules in clean() and turns()).
"""
from __future__ import annotations

import json
import logging
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
TURN_CAP = 4000  # characters a logged turn keeps: a long paste or report is not memory (the tagger batches 6,000)
LOG = logging.getLogger("uvicorn.error")  # where uvicorn writes, so it lands in .memory/server.log

# What clean() drops. A wrapper is any closed <x-y> or <x_y> block: Claude Code's (system-reminder, task-notification,
# command-name, local-command-stdout, bash-input, pasted_content, user-prompt-submit-hook, ...) and our own.
WRAPPER = re.compile(r"<([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)\b[^>]*>.*?</\1\b[^>]*>", re.S)  # </pasted_content id=..>
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)  # an unclosed fence runs to the end
MARKER = re.compile(r"^\s*\[Request interrupted by user[^\]]*\]\s*$|\[Image #\d+\] ?", re.M)
NOISE = re.compile(r"""^\s*(?:
      [{\[]\s*(?:["{\[\d-]|$) | [}\]]                       # JSON: a line that opens or closes an object or array
    | "[^"\n]{1,80}"\s*:                                  # JSON: "key": ...
    | \d{4}-\d\d-\d\d[T\s]\d\d:\d\d | \d\d:\d\d:\d\d[.,\s]     # a log line's timestamp
    | \[?(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b[\]:]   # a log level
    | Traceback\ \(most\ recent | File\ "[^"]+",\ line\ \d+ | at\ \S+\ \(\S+:\d+(?::\d+)?\)   # stack frames
    | \$\ \S | \S+@\S+:\S*\$ | >>>\ | \+\+\+\ | ---\ [ab/] | @@\ -\d   # a shell prompt, a REPL, a diff
    | .*[│┃┆].*[│┃┆]                                    # a terminal table row
    )""", re.X)
SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|A[KS]IA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,})\b"  # API keys
                    r"|\b[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)[A-Z0-9_]*\s*[=:]\s*\S+")  # NAME=value


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


def clean(text: str) -> str:
    """The prose of one message: no wrapper blocks (system reminders, task notifications, pastes, our own blocks), no
    fenced code, no line that looks like JSON, a log, a stack trace, a shell or diff line or a terminal table, no line
    that is mostly not letters, keys and tokens masked, and at most TURN_CAP characters, cut at a space."""
    text = MARKER.sub("", FENCE.sub("", WRAPPER.sub("", OURS.sub("", text))))
    keep = [x for x in text.split("\n") if not NOISE.match(x)
            and not (len(x.strip()) >= 20 and sum(c.isalpha() or c.isspace() for c in x) < 0.6 * len(x))]
    text = SECRET.sub("[secret]", re.sub(r"\n{3,}", "\n\n", "\n".join(keep))).strip()
    if len(text) > TURN_CAP:
        text = text[:text.rfind(" ", 0, TURN_CAP) if " " in text[:TURN_CAP] else TURN_CAP].rstrip() + " …"
    return text


def turns(path: str | Path) -> list[dict]:
    """The conversation in a Claude Code transcript, one entry per turn, consecutive parts of one role merged: the
    user's own words, and the assistant's final prose (its text after the turn's last tool call; tool calls, tool
    results, thinking and subagents are left out). Each part is clean()ed. A part that cleans to nothing (a task
    notification, a paste) still holds its turn's place, as an empty entry: Store.ingest counts places to find what is
    new, so that count must not change from what earlier versions logged; it does not log empty turns."""
    out: list[dict] = []
    tool = False  # a tool call since the assistant's last text: its next text is later prose, which replaces the narration
    for line in jsonl(Path(path).read_text(encoding="utf-8", errors="replace")):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") not in ("user", "assistant") \
                or e.get("isMeta") or e.get("isSidechain") or e.get("isCompactSummary"):
            continue
        c = (e.get("message") or {}).get("content")
        blocks = [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []
        called = any(b.get("type") in ("tool_use", "tool_result") for b in blocks)
        text = c if isinstance(c, str) else "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        text = OURS.sub("", text).strip()
        if not text or text.startswith(COMMAND):  # what earlier versions skipped, so the places line up
            tool = tool or called
            continue
        text = clean(text)
        if out and out[-1]["role"] == e["type"]:
            if e["type"] == "assistant" and tool and text:
                out[-1]["content"] = text
            elif text:
                out[-1]["content"] = (out[-1]["content"] + "\n\n" + text).strip()
        else:
            out.append({"role": e["type"], "content": text})
        tool = called
    return out


def payload(body: bytes) -> dict:
    """The hook's stdin JSON. An empty body is {} (nothing to do); a raw control character inside a string is let
    through. ValueError (the API's 400) with the reason for a body that is not one JSON object."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        p = json.loads(text, strict=False)
    except ValueError as e:
        raise ValueError(f"the body is not JSON: {getattr(e, 'msg', e)} at character {getattr(e, 'pos', '?')} of "
                         f"{len(text)}") from None
    if not isinstance(p, dict):
        raise ValueError(f"the body is a JSON {type(p).__name__}, not an object")
    return p


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
        last = clean(str(p.get("last_assistant_message") or ""))
        if conversation and last and event == "stop":  # the file can lag the final reply at Stop
            if conversation[-1]["role"] != "assistant":
                conversation.append({"role": "assistant", "content": last})
            else:  # the final prose, whatever the file holds of the turn so far
                conversation[-1]["content"] = last
        if conversation:  # Stop runs after the reply was sent: it may wait for a rebuild
            store.ingest(sid, conversation, agent="claude-code", tail=True, patient=event == "stop")
    if event == "pre-compact":
        store.compact(sid)
    return {}
