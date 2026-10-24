"""The Memory contract. The runner forks per question: a disk-backed adapter must override fork() and close()."""
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
        # optional: "view_bytes" and "log_bytes"; the runner defaults view to store and log to 0
        raise NotImplementedError

    def fork(self) -> "Memory":  # an independent copy of the ingested state, one per question
        return copy.deepcopy(self)

    def close(self) -> None:  # release files, connections and temp dirs
        pass


def format_session(s: Session) -> str:
    """A session as timestamped text, the form every baseline hands to the reader."""
    return f"[{s.ts} | agent {s.agent}]\n" + "\n".join(f"{t.role}: {t.content}" for t in s.turns)
