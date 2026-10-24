"""LOG and DECISIONS: the two stores that are the truth (research/33 A).

LOG: every turn, verbatim, once. The event id is sha256(agent, session, turn index, role, ts, text), so a
retried append adds nothing (33 A.3). DECISIONS: every model judgement and the route code gave it, written
before it changes anything (33 A.2); FACTS is rebuilt from them with no model call. Both live in one SQLite
file in a temp dir, plus in-memory mirrors for speed; triggers refuse UPDATE and DELETE on both (33 B.6.3).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE log (seq INTEGER PRIMARY KEY, event_hash TEXT NOT NULL UNIQUE, agent TEXT NOT NULL,
                  session_id TEXT NOT NULL, turn_index INTEGER NOT NULL, role TEXT NOT NULL, ts TEXT NOT NULL,
                  text TEXT NOT NULL);
CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, event_hash TEXT NOT NULL REFERENCES log(event_hash),
                        body TEXT NOT NULL);
CREATE TRIGGER log_no_update BEFORE UPDATE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TRIGGER log_no_delete BEFORE DELETE ON log BEGIN SELECT RAISE(ABORT, 'log is append-only'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
"""


@dataclass(frozen=True)
class Event:
    seq: int
    hash: str
    agent: str
    session_id: str
    turn_index: int
    role: str
    ts: str
    text: str


def event_hash(agent: str, session_id: str, turn_index: int, role: str, ts: str, text: str) -> str:
    return hashlib.sha256(json.dumps([agent, session_id, turn_index, role, ts, text], ensure_ascii=False).encode()).hexdigest()


class Log:
    def __init__(self, src: "Log | None" = None):
        # The LOG lives in a file in a temp dir; a fork (one per question) is an in-memory snapshot, so a run with
        # many forks holds no files. close() releases the handle and removes the dir.
        self._tmp = tempfile.TemporaryDirectory(prefix="spookling-log-") if src is None else None
        path = Path(self._tmp.name) / "log.sqlite" if self._tmp else ":memory:"
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        # ponytail: no fsync for the bench's temp copy (rebuilt every run; dump() writes it out). A deployed LOG keeps
        # synchronous=FULL: it is the truth.
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA journal_mode=MEMORY")
        if src is None:
            self.db.executescript(SCHEMA)
            self.events: list[Event] = []
            self.by_hash: dict[str, Event] = {}
            self.decisions: list[dict] = []
        else:  # fork: a snapshot of the file; events and decision dicts are never mutated, so sharing them is safe
            src.db.backup(self.db)
            self.events, self.by_hash, self.decisions = list(src.events), dict(src.by_hash), list(src.decisions)

    def close(self) -> None:
        """Release the SQLite handle and remove the temp dir. Idempotent; the LOG is unusable afterwards."""
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def append(self, agent: str, session_id: str, turn_index: int, role: str, ts: str, text: str) -> Event | None:
        """The new event, or None when this exact turn is already in the LOG."""
        h = event_hash(agent, session_id, turn_index, role, ts, text)
        if h in self.by_hash:
            return None
        cur = self.db.execute("INSERT OR IGNORE INTO log (event_hash, agent, session_id, turn_index, role, ts, text)"
                              " VALUES (?, ?, ?, ?, ?, ?, ?)", (h, agent, session_id, turn_index, role, ts, text))
        ev = Event(cur.lastrowid, h, agent, session_id, turn_index, role, ts, text)
        self.events.append(ev)
        self.by_hash[h] = ev
        return ev

    def decide(self, d: dict) -> dict:
        """Write a decision before anything acts on it. Idempotent by decision_id."""
        self.db.execute("INSERT OR IGNORE INTO decisions (decision_id, event_hash, body) VALUES (?, ?, ?)",
                        (d["id"], d["source"], json.dumps(d, ensure_ascii=False, sort_keys=True)))
        self.decisions.append(d)
        return d

    @property
    def last_seq(self) -> int:
        return self.events[-1].seq if self.events else 0

    def text_bytes(self) -> int:
        """The verbatim turn text only: what PASS-MARKS 8 compares the prompt-facing store against."""
        return sum(len(e.text.encode()) for e in self.events)

    def lines(self) -> tuple[list[str], list[str]]:
        """LOG and DECISIONS as JSON lines (what dump() writes and store_bytes counts)."""
        log = [json.dumps(asdict(e), ensure_ascii=False) for e in self.events]
        return log, [json.dumps(d, ensure_ascii=False, sort_keys=True) for d in self.decisions]

    def dump(self, out: Path) -> None:
        log, dec = self.lines()
        (out / "log.jsonl").write_text("".join(x + "\n" for x in log))
        (out / "decisions.jsonl").write_text("".join(x + "\n" for x in dec))
