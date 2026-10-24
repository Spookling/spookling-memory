"""The Memory contract (memory-bench SPEC.md), and the Session and Turn it is fed.

The interface under test. The runner makes one instance per history and calls
reset() first, then ingest() once per session in order, compact() when the harness
compacts, and retrieve() once per question. An adapter never sees the gold answer.

Questions that share one history (LoCoMo, MemoryAgentBench, updates) are answered on
fork()s of the ingested adapter, one fork per question, so one question's retrieve() can
never change what another sees. The default fork is a deep copy, which is right for
adapters that keep all state in memory. A disk-backed adapter (files on disk, SQLite)
MUST override fork() to snapshot its directory or database into a fresh copy, and close() to release
it: the runner closes every fork after its question and every adapter after its history's questions,
and keeps at most max_concurrency of them alive."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    content: str
    extra: dict = field(default_factory=dict)


@dataclass
class Session:
    id: str
    agent: str
    ts: str  # ISO 8601 with a timezone
    turns: list[Turn]
    extra: dict = field(default_factory=dict)


class Memory:
    name: str

    def reset(self) -> None:
        raise NotImplementedError

    def ingest(self, session: Session) -> None:  # once per session, in order
        raise NotImplementedError

    def compact(self, session_id: str) -> None:  # optional, default no-op
        pass

    def retrieve(self, question: str, asked_by: str, ts: str) -> str:  # text for the prompt, may be ""
        raise NotImplementedError

    def stats(self) -> dict:  # at least {"store_bytes": int}
        # Optional: "view_bytes" (the prompt-facing store: facts, rules, index page) and "log_bytes"
        # (a verbatim log, never loaded whole). The runner defaults view to store and log to 0.
        raise NotImplementedError

    def fork(self) -> "Memory":  # an independent copy of the ingested state, one per question
        return copy.deepcopy(self)

    def close(self) -> None:  # release files, connections and temp dirs; the runner calls it on every
        pass  # adapter and fork as soon as its questions are answered (at most max_concurrency live)


def format_session(s: Session) -> str:
    """A session as timestamped text, the form every baseline hands to the reader."""
    return f"[{s.ts} | agent {s.agent}]\n" + "\n".join(f"{t.role}: {t.content}" for t in s.turns)
