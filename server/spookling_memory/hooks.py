"""Claude Code hooks, server side: the hook scripts POST their stdin here, so the transcript parsing is Python."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from .core.recall import ABSTAIN
from .store import jsonl

EVENTS = ("user-prompt-submit", "pre-compact", "stop")
CAP = 10_000  # Claude Code's cap, in UTF-16 units; a longer value reaches Claude only as a file path
OURS = re.compile(r"<(invariant|recalled-data|memory-index|kept-before-compaction)\b.*?</\1>", re.S)
COMMAND = ("<command-", "<local-command-")  # slash-command wrappers and their output
MNT = Path("/mnt")  # WSL's default automount root
TURN_CAP = 4000
LOG = logging.getLogger("uvicorn.error")  # uvicorn's, so it lands in .memory/server.log

# a wrapper is any closed <x-y> or <x_y> block: Claude Code's and our own
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
    """A Windows path reads as /mnt/<drive>/... in WSL. ponytail: the default root; read /etc/wsl.conf if moved."""
    m = re.match(r"([A-Za-z]):[\\/](.*)", p)
    if m and os.name != "nt" and (MNT / m[1].lower()).is_dir():
        return MNT.joinpath(m[1].lower(), *[x for x in re.split(r"[\\/]", m[2]) if x])
    return Path(p)


def clean(text: str) -> str:
    """The prose of one message: no wrappers, fences or noise lines; secrets masked; at most TURN_CAP characters."""
    text = MARKER.sub("", FENCE.sub("", WRAPPER.sub("", OURS.sub("", text))))
    keep = [x for x in text.split("\n") if not NOISE.match(x)
            and not (len(x.strip()) >= 20 and sum(c.isalpha() or c.isspace() for c in x) < 0.6 * len(x))]
    text = SECRET.sub("[secret]", re.sub(r"\n{3,}", "\n\n", "\n".join(keep))).strip()
    if len(text) > TURN_CAP:
        text = text[:text.rfind(" ", 0, TURN_CAP) if " " in text[:TURN_CAP] else TURN_CAP].rstrip() + " …"
    return text


def turns(path: str | Path) -> list[dict]:
    """One entry per turn; a part that cleans to nothing stays as an empty entry, since Store.ingest counts places."""
    out: list[dict] = []
    tool = False  # a tool call since the assistant's last text: its next text replaces the narration
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
    """{} for an empty body; ValueError (a 400) for a body that is not one JSON object."""
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


def codex_turns(path: Path) -> list[dict]:
    """Codex rollout response_item messages only; never developer context, tool output, reasoning or commentary.
    The rollout format is internal (observed with codex-cli 0.164.0); event_msg duplicates are deliberately skipped.
    Use the same cleaning and whole-transcript tail deduplication as Claude Code.
    """
    out: list[dict] = []
    for line in jsonl(path.read_text(encoding="utf-8", errors="replace")):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") != "response_item":
            continue
        p = e.get("payload")
        if not isinstance(p, dict) or p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
            continue
        role = p["role"]
        if role == "assistant" and p.get("phase") not in (None, "final_answer"):
            continue
        content = p.get("content") or []
        text = content if isinstance(content, str) else "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("input_text", "output_text"))
        # Codex writes its bootstrap instructions and environment as user messages, too.
        if text.lstrip().startswith(("# AGENTS.md instructions", "<environment_context>", "<permissions instructions>")):
            continue
        text = clean(text)
        if out and out[-1]["role"] == role:
            if text:
                out[-1]["content"] = (out[-1]["content"] + "\n\n" + text).strip()
        else:
            out.append({"role": role, "content": text})
    return out


def handle(store, event: str, p: dict) -> dict:
    """What the hook prints. Stop and PreCompact log new turns; PreCompact then keeps the session's rules."""
    agent = "codex" if p.get("harness") == "codex" else "claude-code"
    sid = str(p.get("session_id") or agent)
    if event == "user-prompt-submit":
        prompt = p.get("prompt") or ""
        ctx = store.retrieve(prompt)["context"] if prompt.strip() else ""
        if not ctx or ctx == ABSTAIN:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": cap(ctx)}}
    path = local_path(str(p.get("transcript_path") or ""))
    if path.suffix == ".jsonl" and path.is_file():
        conversation = codex_turns(path) if agent == "codex" else turns(path)
        last = clean(str(p.get("last_assistant_message") or ""))
        if conversation and last and event == "stop":  # the file can lag the final reply at Stop
            if conversation[-1]["role"] != "assistant":
                conversation.append({"role": "assistant", "content": last})
            else:
                conversation[-1]["content"] = last
        if conversation:  # Stop is after the reply: it may wait for a rebuild
            store.ingest(sid, conversation, agent=agent, tail=True, patient=event == "stop")
    if event == "pre-compact":
        store.compact(sid)
    return {}
