"""One memory folder (`.memory/` in a project): the two append-only records, and the adapter's views rebuilt from them.

    log.jsonl         the LOG: every turn, verbatim, once (the adapter's Log.lines() format)
    judgements.jsonl  the judgement log: every adapter decision, plus this server's rulings (compact, reset, reset_undo)
    lock              locked while the folder is open, so one process writes it (an OS lock: a crash never leaves it held)

The adapter keeps its SQLite in a temp dir that close() removes, so these two files are the only durable copy.
Each change appends the new lines and fsyncs. The files are UTF-8 with \n line ends on every OS, so a folder copied
between Windows, macOS and Linux opens unchanged. Opening a folder replays them into a fresh SpooklingMemory (load())
and settle() rebuilds FACTS, EVENTS, the INDEX page and SEARCH; no model is called for what was already judged.
"""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from bench.adapters.spookling import SpooklingMemory
from bench.adapters.spookling import compact as keep
from bench.adapters.spookling.sleep import tx_of
from bench.schema import Session, Turn

from . import reset as R

LOG, JUDGEMENTS = "log.jsonl", "judgements.jsonl"
WINDOWS = os.name == "nt"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jsonl(text: str) -> list[str]:
    """The lines of a JSONL text, split on \\n only (a \\r before it is dropped). str.splitlines() would also split on
    U+2028, U+0085 and the like, which JSON keeps raw inside a string."""
    return [x.removesuffix("\r") for x in text.split("\n")]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(jsonl(path.read_text(encoding="utf-8")), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as e:
            raise ValueError(f"{path} line {n} is not JSON ({e}); the file is left as it is") from None
    return out


def lines(mem, start: tuple[int, int] = (0, 0)) -> tuple[list[str], list[str]]:
    """The LOG and judgement lines from `start` on, serialised as the adapter's Log.lines() does."""
    return ([json.dumps(asdict(e), ensure_ascii=False) for e in mem.log.events[start[0]:]],
            [json.dumps(d, ensure_ascii=False, sort_keys=True) for d in mem.log.decisions[start[1]:]])


def load(mem, events: list[dict], decisions: list[dict]) -> None:
    """Replay the two records into a fresh adapter: the LOG, then every judgement in order, with the side state the
    adapter grows while it judges (key words, event classes, compaction keepers). The views come from settle()."""
    for e in events:
        ev = mem.log.append(e["agent"], e["session_id"], e["turn_index"], e["role"], e["ts"], e["text"])
        if ev is None or ev.hash != e["hash"]:
            raise ValueError(f"LOG line seq {e.get('seq')}: a repeated or altered turn")
    for d in decisions:
        mem.record(d)
        replay(mem, d)
    mem.tag_cursor = mem.log.last_seq  # every stored turn was tagged before it was stored (see Store.ingest)


def replay(mem, d: dict) -> None:
    """What light() and resolve_events() grow beside a judgement (sleep.py), redone from the judgement alone."""
    step = d["step"]
    if step == "triage" and d.get("key_source") == "model" and d["route"] in ("fast", "slow"):
        mem.vocab.add(d["key"], d["cues"])
    elif step == "event" and d["route"] == "fast" and d.get("object") and d.get("object_class") \
            and d["object_class"] not in mem.classes.classes_in(d["object"], "object"):
        head = re.findall(r"[A-Za-z][\w'-]*", d["object"])
        if head:
            mem.classes.propose_word(head[-1], d["object_class"])
    elif step == "event_resolve" and (src := mem.decision_by_id.get(d["of"])):
        for field, kind in (("action_class", "action"), ("object_class", "object")):
            cls, got = src.get(field), d.get(field)
            if cls and not mem.classes.closed(cls) and got and not mem.classes.closed(got):
                word = src["action"] if kind == "action" else (src.get("object") or "")
                mem.classes.add_class(got, kind, [word] if word else [])
    elif step == "compact" and mem.compact_keep:
        mem.staging += [keep.Kept(**k) for k in d["kept"]]


def append(path: Path, new: list[str]) -> None:
    if new:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write("".join(x + "\n" for x in new))
            f.flush()
            os.fsync(f.fileno())


def lock(f) -> None:
    """Lock f's first byte for this open file: fcntl.flock on macOS and Linux, msvcrt.locking on Windows. OSError if
    another open file holds it. The OS drops the lock when the process ends, so a crash never leaves the folder stuck."""
    if WINDOWS:
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(f) -> None:
    """Release and close. Windows may free a closed handle's lock late, so it is unlocked first; a reopen right after
    then works. Idempotent."""
    if f.closed:
        return
    if WINDOWS:
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    f.close()


class Store:
    """An open memory folder. Every call holds one lock (ponytail: one writer, one reader at a time; a per-view
    read lock if retrieval latency under load matters). `adapter` kwargs go to SpooklingMemory (model, triage, ...)."""

    def __init__(self, folder: str | Path, **adapter):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lockfile = open(self.folder / "lock", "ab")  # never truncated: another process may hold its lock
        try:
            lock(self._lockfile)
        except OSError:
            self._lockfile.close()
            raise RuntimeError(f"{self.folder} is open in another process (one server per memory folder)") from None
        self.adapter, self.lock, self._post = adapter, threading.RLock(), None
        try:
            events, decisions = read_jsonl(self.folder / LOG), read_jsonl(self.folder / JUDGEMENTS)
            self.mem = SpooklingMemory(**adapter)
            load(self.mem, events, decisions)
            self.written = (len(events), len(decisions))
            self.mem.settle()
            self._flush()  # a full sleep may judge items left pending: those judgements are records too
        except BaseException:
            unlock(self._lockfile)
            raise

    @contextmanager
    def _busy(self):
        """The store's one lock, and a model layer that cannot stop the server: bench.models calls sys.exit() when a
        provider key is missing, which in a server thread kills the process."""
        with self.lock:
            try:
                yield
            except SystemExit as e:
                raise RuntimeError(f"the writer model could not be called: {e}") from None

    def _flush(self) -> None:
        log, dec = lines(self.mem, self.written)
        append(self.folder / LOG, log)  # the LOG first: judgements point into it
        append(self.folder / JUDGEMENTS, dec)
        self.written = (len(self.mem.log.events), len(self.mem.log.decisions))

    def records(self) -> tuple[list[dict], list[dict]]:
        return [asdict(e) for e in self.mem.log.events], list(self.mem.log.decisions)

    def ingest(self, session_id: str, turns: list[dict], ts: str | None = None, agent: str = "user",
               tail: bool = False) -> int:
        """Log one session's turns; returns how many were new. tail=True: `turns` is the whole conversation so far;
        only the turns past those already logged for session_id are logged, as session "<id>#<n>" stamped now (so
        the LOG's time order follows arrival, which the fold needs, and a retried call adds nothing)."""
        with self._busy():
            m = self.mem
            if tail:
                have = sum(1 for e in m.log.events if e.session_id == session_id or e.session_id.startswith(session_id + "#"))
                if have >= len(turns):
                    return 0
                turns, session_id, ts = turns[have:], f"{session_id}#{have}", None
            ts = ts or now()
            tx_of(ts, 0, 0)  # a bad timestamp is refused here: in the LOG it would fail every later sleep
            if not R.admits(m.log.decisions, ts):
                raise ValueError(f"session {session_id} at {ts} predates the reset at {R.latest(m.log.decisions)['at']}")
            n = len(m.log.events)
            m.ingest(Session(session_id, agent, ts, [Turn(t["role"], t["content"]) for t in turns]))
            # only now, with the session tagged, is it written: a failed tagging call leaves the files as they were
            self._flush()
            return len(m.log.events) - n

    def retrieve(self, question: str, ts: str | None = None) -> dict:
        """{context, digest, strong}. After a reset, a question about now is answered from the after-reset view."""
        ts = ts or now()
        with self._busy():
            self.mem.settle()
            self._flush()
            r = R.latest(self.mem.log.decisions)
            if r and not R.asks_before(self.mem, question, ts, r):
                return self._view(r).retrieve(question, "user", ts)
            with R.unpinned(self.mem, r):
                return self.mem.retrieve(question, "user", ts)

    def _view(self, r: dict) -> SpooklingMemory:
        """The memory rebuilt from the records after reset r, cached until the records grow.
        ponytail: rebuilt from zero after every change, as the adapter's own full sleep is; grow it in place (ingest the
        new sessions into it) if a long after-reset history makes the first question after a change slow."""
        key = (r["id"], len(self.mem.log.events), len(self.mem.log.decisions))
        if self._post is None or self._post[0] != key:
            if self._post is not None:
                self._post[1].close()
            post = SpooklingMemory(**self.adapter)
            load(post, *R.after(*self.records(), r))
            post.settle()  # the main memory settled first, so nothing is left for a model here
            self._post = (key, post)
        return self._post[1]

    def compact(self, session_id: str) -> dict:
        """Before the harness summarises a session: keep its rules, decisions, values, dates, paths and names verbatim
        (the adapter's compact(), with session_id also matching its "<id>#<n>" tail parts), log what was kept as a
        "compact" ruling, and return the survivor block: the <invariant> every later context leads with."""
        with self._busy():
            m = self.mem
            evs = [e for e in m.log.events if e.session_id == session_id or e.session_id.startswith(session_id + "#")]
            have = {k.text for k in m.staging}
            new = [k for k in keep.extract(evs) if k.text not in have] if m.compact_keep else []
            if new:
                m.record({"id": R._id("compact", session_id, m.log.last_seq, len(m.log.decisions)), "step": "compact",
                          "session": session_id, "source": evs[-1].hash, "kept": [asdict(k) for k in new], "created": now()})
                m.staging += new
                self._flush()
            with R.unpinned(m, R.latest(m.log.decisions)):
                return {"block": m.invariant_block(), "kept": len(new)}

    def reset(self, note: str = "", at: str | None = None) -> dict:
        with self._busy():
            out = R.reset(self.mem, at or now(), note)
            self._flush()
            return out

    def undo_reset(self) -> dict:
        with self._busy():
            out = R.undo_reset(self.mem)
            self._flush()
            return out

    def restore(self, events: list[dict], decisions: list[dict]) -> dict:
        """Load an archive's records into this (empty) memory and rebuild every view."""
        with self._busy():
            if self.mem.log.events or self.mem.log.decisions:
                raise ValueError("this memory is not empty: import into an empty folder")
            load(self.mem, events, decisions)
            self.mem.settle()
            self._flush()
            return {"events": len(self.mem.log.events), "judgements": len(self.mem.log.decisions)}

    def export(self, path) -> dict:
        from .portability import export

        with self._busy():
            return export(self.mem, path)

    def stats(self) -> dict:
        with self._busy():
            r = R.latest(self.mem.log.decisions)
            return self.mem.stats() | {"folder": str(self.folder), "before_reset": len(R.before_reset(self.mem)),
                                       "reset": {k: r[k] for k in ("id", "at", "note", "upto")} if r else None}

    def close(self) -> None:
        with self._busy():
            if self._post is not None:
                self._post[1].close()
                self._post = None
            self.mem.close()
            unlock(self._lockfile)
