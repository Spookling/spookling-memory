"""Reset: a ruling the fold replays (facts closed at T) and an after-reset view for retrieval. Nothing is deleted."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import date, datetime, timezone

from .core import sleep
from .core.dates import day_of
from .core.facts import FOREVER, Facts
from .core.recall import anchor_event, date_in  # the package's `recall` is the function, not the module
from .core.sleep import tx_of

PINNED = frozenset({"rule", "decision", "failed", "open"})  # the kinds <invariant> pins


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(*parts) -> str:
    return hashlib.sha256("\x00".join(map(str, parts)).encode()).hexdigest()


def active(decisions: list[dict]) -> list[dict]:
    """Reset rulings not undone, in LOG order."""
    on: list[dict] = []
    for d in decisions:
        if d["step"] == "reset":
            on.append(d)
        elif d["step"] == "reset_undo":
            on = [r for r in on if r["id"] != d["of"]]
    return on


def latest(decisions: list[dict]) -> dict | None:
    on = active(decisions)
    return on[-1] if on else None


def tx(r: dict) -> str:
    """The reset's transaction time: after every decision on a turn at or before its LOG position."""
    return tx_of(r["at"], r["upto"], 999)


def day(r: dict) -> date:
    return date.fromisoformat(tx(r)[:10])


def cut(facts: Facts, r: dict) -> None:
    """Close every believed row open at T at T's day; nothing is deleted."""
    t, d = tx(r), tx(r)[:10]
    for attr, item in facts.db.execute("SELECT DISTINCT attribute, item FROM facts WHERE superseded_at=? AND valid_to>?"
                                       " ORDER BY 1, 2", (FOREVER, d)).fetchall():
        facts.put(attr, item, None, d, FOREVER, t, {}, insert=False)


_fold = sleep.fold


def fold(decisions: list[dict]) -> Facts:
    """sleep.fold with each active reset's cut applied at its LOG position."""
    cuts = sorted(active(decisions), key=lambda r: r["upto"])
    if not cuts:
        return _fold(decisions)
    final = {}  # sleep.fold's pick: a resolve beats its triage; a triage counts only if fast
    for d in decisions:
        if d["step"] in ("triage", "resolve") and (d["step"] == "resolve" or d["route"] == "fast"):
            final[(d["seq"], d["idx"])] = d
    facts = Facts()
    for k in sorted(final):
        while cuts and k[0] > cuts[0]["upto"]:
            cut(facts, cuts.pop(0))
        facts.apply(final[k])
    for r in cuts:
        cut(facts, r)
    return facts


# ponytail: patches sleep.fold process-wide (sleep.full looks it up per call); goes when the adapter's fold knows resets
sleep.fold = fold


def _refold(mem) -> None:
    mem.full_seq = -1  # settle() runs a full sleep only when the LOG grew
    mem.settle(tag=False)  # a refold of what is judged: the callers settled first


def _source(mem) -> str:
    return mem.log.events[-1].hash if mem.log.events else ""


def reset(mem, at: str, note: str = "", tag: bool = True) -> dict:
    """`at` may not predate a turn already in the LOG. tag=False: the caller tagged first (Store, with its mark)."""
    t = tx_of(at, 0, 0)  # also validates the timestamp
    late = [e.ts for e in mem.log.events if tx_of(e.ts, 0, 0) > t]
    if late:
        raise ValueError(f"a reset at {at} would predate turns already recorded (latest {max(late)})")
    mem.settle(tag)  # judge what is pending first, so those judgements fall before the ruling
    r = {"id": _id("reset", at, mem.log.last_seq, len(mem.log.decisions)), "step": "reset", "at": at,
         "upto": mem.log.last_seq, "note": note, "source": _source(mem), "created": at}
    mem.record(r)
    _refold(mem)
    return r


def undo_reset(mem, tag: bool = True) -> dict:
    r = latest(mem.log.decisions)
    if r is None:
        raise ValueError("no reset to undo")
    mem.settle(tag)
    u = {"id": _id("reset_undo", r["id"], len(mem.log.decisions)), "step": "reset_undo", "of": r["id"],
         "source": _source(mem), "created": now()}
    mem.record(u)
    _refold(mem)
    return u


def before_reset(mem) -> set[str]:
    """Hashes of the LOG turns before the latest reset."""
    r = latest(mem.log.decisions)
    return {e.hash for e in mem.log.events if e.seq <= r["upto"]} if r else set()


def admits(decisions: list[dict], ts: str) -> bool:
    """A session said before the latest reset cannot enter: put() refuses a change older than the rows it closes."""
    r = latest(decisions)
    return r is None or tx_of(ts, 0, 0) >= tx_of(r["at"], 0, 0)


def asks_before(mem, question: str, ts: str, r: dict) -> bool:
    """The question's date (as recall reads it) starts before the reset's day."""
    anchor = (lambda tail: anchor_event(mem, tail)) if mem.events_on else None
    when = date_in(question, day_of(ts), mem.facts, mem.values, mem.vocab, mem.date_fixes, mem.anchor_now,
                          mem.since_range, anchor)
    return when.how != "now" and (when.start or when.as_of) < day(r)


def after(events: list[dict], decisions: list[dict], r: dict) -> tuple[list[dict], list[dict]]:
    """The records said after reset r (LOG lines and judgements), for the after-reset view."""
    evs = [e for e in events if e["seq"] > r["upto"]]
    hashes = {e["hash"] for e in evs}
    out = []
    for d in decisions:
        if d["step"] == "compact":
            d = {**d, "kept": [k for k in d["kept"] if k["seq"] > r["upto"]]}
        elif d["step"] in ("reset", "reset_undo") or d["source"] not in hashes:
            continue
        out.append(d)
    return evs, out


@contextmanager
def unpinned(mem, r: dict | None):
    """For the length of a call, rules and pinned keepers from before reset r are left out of the context head."""
    if r is None:
        yield
        return
    staging, rules = mem.staging, mem.facts.rules
    seq = lambda h: mem.log.by_hash[h].seq if h in mem.log.by_hash else 0
    mem.staging = [k for k in staging if k.seq > r["upto"] or k.kind not in PINNED]
    mem.facts.rules = [x for x in rules if seq(x["source"]) > r["upto"]]
    try:
        yield
    finally:
        mem.staging, mem.facts.rules = staging, rules
