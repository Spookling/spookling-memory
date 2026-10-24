"""SEARCH and recall(): research/32 section C, with the time rule of research/34 (2026-10-13).

Units are a user turn plus the reply. Each unit is indexed under its own words plus the cue words of every
key whose known value it names (index-time expansion, value -> attribute only). A question is read for a
date first (absolute or relative to now), then for an anchor ("when I got the iPhone 16" -> that row's
valid_from), else it means now. Keys named by its words or values are looked up at that date (a time slice,
no ranking); text search runs too, filtered to units said before the date's end only when a date or anchor
is named. Hits are diversified per key, dated, put in time order, and the fact records go last, next to
the question. Vectors are off: `dense` is the hook for an embedder, and RRF fuses when it is set.
"""
from __future__ import annotations

import calendar
import math
import re
import threading
from dataclasses import dataclass
from datetime import date, timedelta

from rank_bm25 import BM25Okapi

from . import dates, mapreduce
from . import timeline as TIME
from .complex_triggers import categories
from .dates import FOREVER as FOREVER_DATE
from .dates import fmt
from .entities import Entities
from .events import stem
from .facts import Facts, Row, norm
from .index_page import TASTES, hidden
from .triage import Vocab, sentence_of

ABSTAIN = "Nothing on record about this."
# Aggregation questions (count, sum or list across sessions; v1.2, repaired in v1.3): more hits, their own
# budget, each hit with its neighbours in rank order, long assistant replies trimmed, quantities first.
# The aggregation detector is the `aggregate` category of complex_triggers (v2: one table for both).
QUANTITY = re.compile(r"\b(\d+(?:[.,:]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twice|once|dozen"
                      r"|couple)\b", re.I)
NUMBER_BOOST = 1.25  # aggregation questions: a unit stating a quantity ranks this much higher
STRONG = 0.5  # a unit holding this share of the question's terms (idf-weighted) is about the question's subject
READER_FACTOR = 1.25  # reader tokens per cl100k token (the harness's TOKEN_FACTOR, measured on Anthropic readers)
RUNG2_TOKENS = 30000  # the ladder's rung 2 shows one chat in full, cut here to stay inside any reader window
GUIDE = ("These records bear on the question. Answer from what is here, and say what is missing; say I don't know"
         " only if nothing here answers any part of it.")
STOP = set("""a an the and or but if of to in on at by for with from as is am are was were be been being do does did
have has had i me my mine you your it its this that these those what which who whom whose where when why how s t d ll m
re ve not no so just also too very can could would should will shall may might must there here than then now currently
current days moment right still again any some all""".split())
CONNECTIVE = r"\b(when|while|after|before|since|by the time|at the time|around the time)\b"
# v3: a question that also asks about now ("... when I started? How many now?") keeps the turns after its anchor
NOW_TOO = re.compile(r"\b(?:now|currently|these days|at the moment|today|nowadays|still)\b", re.I)
INTERROGATIVE = re.compile(r"\s+(?:did|do|does|was|were|is|are|will|would|has|have|had|can|could|should|might|may)\b", re.I)
# Questions about what the assistant said keep its replies whole (v2 trims them everywhere else)
ASKS_ASSISTANT = re.compile(r"\b(?:what did you|did you (?:say|suggest|recommend|mention|tell)|you (?:suggested|recommended|said|told|"
                            r"mentioned|gave|listed|wrote|shared|provided|offered|explained|described)|remind me|our (?:previous|last|earlier)"
                            r" (?:chat|conversation|discussion)|(?:previous|last|earlier) (?:chat|conversation)|you were)\b", re.I)
_enc = None


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.lower()) if w not in STOP]


def reader_tokens(text: str) -> int:
    """What a reader is likely charged for text: cl100k tokens x READER_FACTOR (offline, deterministic; the count_tokens
    endpoint would be exact but is a network call per context)."""
    return math.ceil(READER_FACTOR * n_tokens(text))


def n_tokens(text: str) -> int:
    global _enc
    if _enc is None:
        import tiktoken

        _enc = tiktoken.get_encoding("cl100k_base")
    return len(_enc.encode_ordinary(text))


@dataclass(frozen=True)
class Unit:
    seq: int
    day: str
    text: str
    keys: frozenset
    session: str
    lo: int  # its turns are positions lo..hi of its session's turns
    hi: int
    event: str | None = None  # v2: the first date the user's turn names, resolved against the chat's date
    kind: str = "turn"  # algorithm v2 (research/37 E3): "turn", or "event" (one clause of a user turn, with its class cue)
    eid: str | None = None  # the EVENTS row of an event unit
    occ: tuple | None = None  # (occurred_from, occurred_to) of an event unit: when it HAPPENED


class Values:
    """Every way a believed value has been said, mapped back to (attribute, item, normalised value): the stored
    value, the spans the tagger tied to it, the head of an appositive ("Quillmate, a journaling app" ->
    Quillmate; "a greyhound called Juno" -> Juno) and, case-sensitively, a capitalised word of 4+ letters of a
    multi-word value when no other value has it ("Tidewell Analytics" -> Tidewell)."""

    def __init__(self, facts: Facts):
        ci: dict[str, set] = {}
        cs: dict[str, set] = {}
        for attr in facts.attributes():
            for r in facts.believed(attr):
                if r.value is None:
                    continue
                ref = (attr, r.item, norm(r.value))
                for s in {r.value, *facts.surfaces.get(ref, ())}:
                    ci.setdefault(s.lower(), set()).add(ref)
                head = re.split(r",| called ", r.value)
                for s in (head[0], head[-1]) if len(head) > 1 else ():
                    if len(s.strip()) >= 3:
                        ci.setdefault(s.strip().lower(), set()).add(ref)
                toks = r.value.split()
                if len(toks) > 1:
                    for t in toks:
                        if len(t) >= 4 and t[:1].isupper() and t.isalpha():
                            cs.setdefault(t, set()).add(ref)
        cs = {k: v for k, v in cs.items() if len({x[2] for x in v}) == 1 and k.lower() not in ci}
        self.ci, self.cs = ci, cs
        alt = lambda keys: r"(?<!\w)(" + "|".join(sorted(map(re.escape, keys), key=len, reverse=True)) + r")(?!\w)"
        self._ci = re.compile(alt(ci), re.I) if ci else None
        self._cs = re.compile(alt(cs)) if cs else None

    def find(self, text: str) -> list[tuple[int, str, set]]:
        """(position, surface, refs) for every value named in text."""
        out = []
        for rx, table, low in ((self._ci, self.ci, True), (self._cs, self.cs, False)):
            if rx is not None:
                out += [(m.start(), m.group(1), table[m.group(1).lower() if low else m.group(1)]) for m in rx.finditer(text)]
        return sorted(out, key=lambda x: x[0])


class Search:
    """BM25 (rank_bm25) over units, rebuilt by the full sleep. Immutable once built, so forks share it."""

    def __init__(self, events, values: Values, vocab: Vocab, event_dates: bool = False, entities: bool = False,
                 embed_model: str | None = None, store=None, stem: bool = True):
        self.stem = stem
        self.sessions: dict[str, list] = {}  # session id -> its turns in LOG order (for neighbours)
        units, cur = [], None
        for e in events:
            turns = self.sessions.setdefault(e.session_id, [])
            turns.append(e)
            if e.role == "assistant" and cur is not None and cur[1] == e.session_id:
                cur[3].append(f"assistant: {e.text}")
                cur[5] = len(turns) - 1
                continue
            cur = [e.seq, e.session_id, e.ts[:10], [f"{e.role}: {e.text}"], len(turns) - 1, len(turns) - 1]
            units.append(cur)
        # v2: dates the user's turns name ("last Tuesday"), resolved by the date grammar against the chat's date
        self.events: dict[tuple[str, int], list[tuple[str, dates.Span]]] = {}
        if event_dates:
            for sid, turns in self.sessions.items():
                for i, e in enumerate(turns):
                    if e.role == "user":
                        found = [(sp.text, sp) for sp in dates.find_all(e.text, dates.day_of(e.ts), "nearest")
                                 if not YEAR.search(sp.text) and not (sp.precision == "day" and sp.start == dates.day_of(e.ts))]
                        if found:
                            self.events[(sid, i)] = found[:3]
        self.units = []
        docs = []
        for seq, sid, day, parts, lo, hi in units:
            text = " / ".join(parts)
            keys = frozenset(ref[0] for _, _, refs in values.find(text) for ref in refs)
            ev = self.events.get((sid, lo))
            self.units.append(Unit(seq, day, text, keys, sid, lo, hi, ev[0][1].vf.isoformat() if ev else None))
            docs.append(self.terms(text) + self.terms(" ".join(w for k in sorted(keys) for w in [k.replace("_", " "), *vocab.words.get(k, [])])))
        self.cue_bytes = sum(len(" ".join(sorted(u.keys)).encode()) for u in self.units)  # what expansion adds per unit
        self.bm = BM25Okapi(docs) if any(docs) else None
        self.df: dict[str, int] = {}
        for d in docs:
            for w in set(d):
                self.df[w] = self.df.get(w, 0) + 1
        for w, v in (self.bm.idf.items() if self.bm else ()):  # a small store gives 0 or negative idf; floor it, as files.py
            if v <= 0:
                self.bm.idf[w] = 1e-6
        self.embed_model = embed_model  # v2: exact-cosine dense ranking (research/32 B4), fused by RRF
        self._vecs = None
        self._lock = threading.Lock()
        self.entities = Entities([e.text for turns in self.sessions.values() for e in turns if e.role == "user"]) \
            if entities else None
        self._cost: dict[tuple[str, int], int] = {}
        self._event_units(store)

    def terms(self, text: str) -> list[str]:
        """Index and query terms: stop words out, then Snowball-stemmed ("concerts" finds "concert"; research/37)."""
        ws = words(text)
        return [stem(w) for w in ws] if self.stem else ws

    def _event_units(self, store) -> None:
        """Event-sized units beside the turns (research/37 E3): one per EVENTS row, its verbatim clause plus the class
        words as a cue, pointing at its turn; their own BM25 and dense lists."""
        self.eunits, self.einfo, self.ebm, self._evecs, self.actors = [], [], None, None, set()
        if store is None:
            return
        where = {e.hash: (sid, i) for sid, turns in self.sessions.items() for i, e in enumerate(turns)}
        unit_at = {}
        for k, u in enumerate(self.units):
            for p in range(u.lo, u.hi + 1):
                unit_at.setdefault((u.session, p), k)
        docs = []
        for row in store.rows():
            if row["source_event"] not in where:
                continue
            sid, pos = where[row["source_event"]]
            turn = self.sessions[sid][pos]
            clause = turn.text[row["span_start"]:row["span_end"]]
            classes = {c for c in (row["action_class"], row["object_class"]) if c}
            cue = "; ".join(c.replace("_", " ") for c in (row["action_class"], row["object_class"]) if c)
            text = f"{clause} ({cue})"
            ents = store.entities_of(row["event_id"])
            self.eunits.append(Unit(turn.seq, turn.ts[:10], text, frozenset(), sid, pos, pos, row["occurred_from"], "event",
                                    row["event_id"], (row["occurred_from"], row["occurred_to"])))
            self.einfo.append({"row": row, "classes": classes, "ents": {e for e, _ in ents}, "roles": ents,
                               "turn": unit_at.get((sid, pos)), "clause": clause})
            docs.append(self.terms(text))
        if any(docs):
            self.ebm = BM25Okapi(docs)
            for w, v in self.ebm.idf.items():
                if v <= 0:
                    self.ebm.idf[w] = 1e-6
        self.actors = {r[0] for r in store.db.execute("SELECT DISTINCT entity_id FROM event_entities WHERE role='actor'")}
        self.alias_ids: dict[str, set[str]] = {}
        for alias, eid, _ in store.all_aliases():
            self.alias_ids.setdefault(alias, set()).add(eid)
        rx = lambda keys: re.compile(r"(?<!\w)(" + "|".join(sorted(map(re.escape, keys), key=len, reverse=True)) + r")(?!\w)") if keys else None
        self._alias_rx = rx([a for a in self.alias_ids if a[:1].isupper()])  # names: as written ("Grace", not "grace")
        self._rel_rx = re.compile(rx([a for a in self.alias_ids if not a[:1].isupper()]).pattern, re.I) \
            if any(not a[:1].isupper() for a in self.alias_ids) else None  # relations ("my sister"): any case

    def entities_in(self, text: str) -> set[str]:
        """Entity ids a text names through an alias (the lexical network, C&L assumption 6): a name as written, a
        relation in any case. A speaker (the actor of the turns they say: LoCoMo's "Melanie: ...") is never a cue:
        like the user in a one-person chat, the subject of every event names none of them."""
        out = set()
        for r in (self._alias_rx, self._rel_rx):
            if r is not None:
                for m in r.finditer(text):
                    key = m.group(1) if m.group(1) in self.alias_ids else next(a for a in self.alias_ids if a.lower() == m.group(1).lower())
                    out |= self.alias_ids[key]
        return out - self.actors

    def line(self, session: str, i: int, trim: int = 0, ev: bool = True) -> str:
        """One turn as shown. trim > 0 shortens a long assistant turn: in aggregation mode the facts are in the
        user's turns, and the replies were 87% of a unit's tokens (LongMemEval, 13 Oct)."""
        e = self.sessions[session][i]
        text = e.text if not trim or e.role != "assistant" or len(e.text) <= trim else e.text[:trim].rstrip() + " [...]"
        found = self.events.get((session, i)) if ev else None
        note = " [" + "; ".join(f"{phrase} = {when_of(sp)}" for phrase, sp in found) + "]" if found else ""
        return f"{e.role}{note}: {text}"

    def cost(self, session: str, i: int, trim: int = 0, ev: bool = True) -> int:
        key = (session, i, trim, ev)
        if key not in self._cost:  # a benign race between forks: both write the same number
            self._cost[key] = n_tokens(self.line(session, i, trim, ev)) + 1
        return self._cost[key]

    def around(self, session: str, hits: list[Unit], cap: int) -> set[int]:
        """Session fallback: the session's turns around its hits, at most `cap` turns (all of a short session)."""
        n = len(self.sessions[session])
        pos = sorted(p for h in hits for p in range(h.lo, h.hi + 1))
        if n <= cap:
            return set(range(n))
        start = max(0, min(pos[len(pos) // 2] - cap // 2, n - cap))
        return set(range(start, start + cap)) | set(pos)

    def render(self, session: str, positions: set[int], label: str = "", trim: int = 0, ev: bool = True) -> str:
        """Chosen turns of one session as one dated block; a gap inside it shows as [...]."""
        turns, out, prev = self.sessions[session], [], None
        for i in sorted(positions):
            if prev is not None and i > prev + 1:
                out.append("[...]")
            out.append(self.line(session, i, trim, ev))
            prev = i
        return f"[{fmt(date.fromisoformat(turns[0].ts[:10]))}]{f' ({label})' if label else ''} " + "\n".join(out)

    def coverage(self, query: str, u: Unit) -> float:
        """Share of the question's terms that the unit holds, each weighted by a smoothed idf (Lucene's, positive even
        for a term the store never saw, which counts against the unit: the question names something not here)."""
        n = len(self.units)
        idf = {t: math.log(1 + (n - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5)) for t in set(self.terms(query))}
        total = sum(idf.values())
        return sum(v for t, v in idf.items() if t in set(self.terms(u.text))) / total if total else 0.0

    def _bm25(self, query: str, prefer=None) -> list[int]:
        q = self.terms(query)
        if self.bm is None or not q:
            return []
        scores = self.bm.get_scores(q)
        if prefer is not None:
            scores = [x * prefer(u) for x, u in zip(scores, self.units, strict=True)]
        return [i for i in sorted(range(len(self.units)), key=lambda i: (-scores[i], i)) if scores[i] > 0]

    def _dense(self, query: str, n: int) -> list[int]:
        """Exact cosine over every unit (no ANN: research/32 B4); unit vectors computed once per build."""
        from . import vectors

        with self._lock:
            if self._vecs is None:
                self._vecs = vectors.embed([u.text[:2000] for u in self.units], self.embed_model)
        if not len(self.units):
            return []
        sims = self._vecs @ vectors.embed([query], self.embed_model, query=True)[0]
        return [int(i) for i in (-sims).argsort(kind="stable")[:n]]

    def _ebm25(self, query: str) -> list[int]:
        q = self.terms(query)
        if self.ebm is None or not q:
            return []
        scores = self.ebm.get_scores(q)
        return [i for i in sorted(range(len(self.eunits)), key=lambda i: (-scores[i], i)) if scores[i] > 0]

    def _edense(self, query: str, n: int) -> list[int]:
        from . import vectors

        if not self.eunits:
            return []
        with self._lock:
            if self._evecs is None:
                self._evecs = vectors.embed([u.text for u in self.eunits], self.embed_model)
        sims = self._evecs @ vectors.embed([query], self.embed_model, query=True)[0]
        return [int(i) for i in (-sims).argsort(kind="stable")[:n]]

    def search(self, query: str, end: str | None = None, n: int = 100, prefer=None, extra=(), dense: bool = False,
               start: str | None = None, more=(), event_end: str | None = None) -> list[Unit]:
        """Ranked units. Turn ranking: plain BM25 alone (v1), or with dense ranking and/or extra queries (entity
        expansions, rewrites: (text, weight)) fused by reciprocal rank fusion, c = 60, each list scaled by its weight
        (research/32 B5). With event units (research/37 E3), the turn ranking becomes one list among: dense over
        event units 1.0, turns 0.5, stemmed BM25 over event units 0.25, and `more` ((list of ("t"|"e", index),
        weight): direct joins, activation). Time: turn units said before `end`; event units whose HAPPENED period
        overlaps [start, event_end or end). No filter is the fallback when it leaves nothing (research/32 B7)."""
        order = self._turn_order(query, n, prefer, extra, dense)
        if not self.eunits:
            if end:
                kept = [i for i in order if self.units[i].day < end]
                order = kept or order
            return [self.units[i] for i in order[:n]]
        # v2.1: two levels. The event lists (BM25, dense, direct joins, activation) are fused into ONE event ranking
        # first, then fused with the turn ranking as equals: four event lists stacked against one turn list let 60 of
        # 60 hits be event units and dropped the turn ranking's best units (LongMemEval 0ea62687, 3a704032)
        ev_lists = [([("e", i) for i in self._ebm25(query)[:n]], W_EBM25)]
        if dense and self.embed_model:
            ev_lists.append(([("e", i) for i in self._edense(query, n)], W_EDENSE))
        ev_lists += [(lst, w) for lst, w in more if lst]
        lists = [([("t", i) for i in order[:n]], W_TURNS), (rrf(ev_lists, n), W_EVENTS)]
        fused: dict[tuple, float] = {}
        for rank, w in lists:
            for pos, key in enumerate(rank[:n]):
                fused[key] = fused.get(key, 0.0) + w / (60 + pos + 1)
        keys = sorted(fused, key=lambda k: (-fused[k], k))
        if end or start:  # v2.1: the no-filter fallback per kind, as v5 had it for turns; an "on or before" event passes
            stop = event_end or end  # every window, so one shared fallback let surviving events drop every turn (0ea62687)
            kt = [k for k in keys if k[0] == "t" and (not end or self.units[k[1]].day < end)]
            ke = [k for k in keys if k[0] == "e" and self._overlaps(self.eunits[k[1]], start, stop)]
            if kt or ke:  # turns fall back to unfiltered on their own; an event outside the window never shows
                keep = set(kt or [k for k in keys if k[0] == "t"]) | set(ke)
                keys = [k for k in keys if k in keep]
        return [self.units[i] if kind == "t" else self.eunits[i] for kind, i in keys[:n]]

    @staticmethod
    def _overlaps(u: Unit, start: str | None, end: str | None) -> bool:
        """An event's happened period [from, to) against [start, end); "on or before" (no from) can be any earlier day."""
        a, b = u.occ or (None, None)
        if end and a and a >= end:
            return False
        return not (start and b and b <= start)

    def _turn_order(self, query: str, n: int, prefer, extra, dense) -> list[int]:
        lists = [(self._bm25(query, prefer), 1.0)]
        if dense and self.embed_model:
            lists.append((self._dense(query, n), 1.0))
        for q, w in extra:
            lists.append((self._bm25(q, prefer), w))
            if dense and self.embed_model:
                lists.append((self._dense(q, n), w))
        if len(lists) == 1:
            return lists[0][0]
        fused: dict[int, float] = {}
        for rank, w in lists:
            for pos, i in enumerate(rank[:n]):
                fused[i] = fused.get(i, 0.0) + w / (60 + pos + 1)
        return sorted(fused, key=lambda i: (-fused[i], i))


YEAR = re.compile(r"\b(?:19|20)\d\d\b")
# research/37 E3 fusion weights, set on the generated two-hop suite (never on LongMemEval): the simulation's dense-event
# 1.0 over turns 0.5 lost one-hop evidence there (look-alike clauses crowd the turns out), so turns lead; activation at
# 0.5 left its typed-path hits at fused rank 25-40, outside the budget, so it weighs as a direct join
W_EDENSE, W_TURNS, W_EBM25, W_DIRECT, W_PPR = 0.5, 1.0, 0.25, 1.0, 1.0
W_EVENTS = 1.0  # v2.1: the fused event ranking against the turn ranking, as equals


def rrf(lists: list[tuple[list, float]], n: int) -> list:
    """Weighted reciprocal rank fusion (c = 60) of ranked key lists, best first."""
    fused: dict = {}
    for rank, w in lists:
        for pos, key in enumerate(rank[:n]):
            fused[key] = fused.get(key, 0.0) + w / (60 + pos + 1)
    return sorted(fused, key=lambda k: (-fused[k], k))
TIMELINE_HITS = 12  # the timeline shows the events among this many best hits: 18-36 lines crowded the turns out (LoCoMo)
PREF_ATTRS = TASTES


def when_of(sp: dates.Span) -> str:
    if sp.precision == "day":
        return fmt(sp.vf)
    if sp.precision == "week":
        return f"week of {fmt(sp.start)}"
    if sp.precision == "month" and not sp.use_point:
        return f"{calendar.month_abbr[sp.start.month]} {sp.start.year}"
    return f"about {fmt(sp.vf)}" if sp.use_point else f"{fmt(sp.start)} to {fmt(sp.end - timedelta(days=1))}"


@dataclass(frozen=True)
class When:
    as_of: date
    start: date | None  # a period to look up ("June 2026"), else None: the point as_of
    end: date | None
    window: date | None  # text units said on or after this day are dropped; None: no filter
    how: str  # absolute | relative | anchor | now
    label: str
    day: date | None = None  # a named single day ("on May 3", "last Saturday"): its turns go first, later ones stay


SINCE = re.compile(r"\bsince\s+(.+)$", re.I)


def since_in(q: str, now: date, facts: Facts, values: Values, anchor_event=None) -> When | None:
    """research/37 E3: "since X" is a range from X to now, never a point. X is a date ("since March"), a stored
    row ("since I got the Fairphone"), or an event in EVENTS ("since I started painting classes")."""
    m = SINCE.search(q)
    if not m:
        return None
    tail = m.group(1).rstrip("?.! ")
    start = None
    sp = dates.parse(tail, now, "past")
    if sp:
        start = sp.start
    if start is None:
        for _, surface, refs in values.find(" ".join(tail.split()[:8])):
            rows = [r for attr, item, nv in sorted(refs) for r in facts.believed(attr, item) if r.value and norm(r.value) == nv]
            if rows:
                start = date.fromisoformat(min(r.valid_from for r in rows))
                break
    if start is None and anchor_event is not None:
        start = anchor_event(tail)
    if start is None or start > now:
        return None
    return When(now, start, now + timedelta(days=1), None, "since", f"since {fmt(start)}")


def date_in(q: str, now: date, facts: Facts, values: Values, vocab: Vocab, fixes: bool = True, now_too: bool = True,
            since: bool = False, anchor_event=None) -> When:
    """The time a question asks about: a date it names (absolute, or relative to now), else an anchor in the
    stored rows, else now (the single open row per key: a direct lookup, never a guess).
    fixes (v2, error analysis of v1.3): a named single day keeps the turns said after it (the report of "last night"
    comes the next day) and puts that day's turns first; a question's own "When did ..." is never an anchor."""
    if since:  # algorithm v2: "since X" is a range to now
        rng = since_in(q, now, facts, values, anchor_event)
        if rng:
            return rng
    span = dates.parse(q, now, "past")
    if span:
        if fixes and span.precision == "day":
            return When(span.point, None, None, None, span.kind, f"in force on {fmt(span.point)}", span.start)
        if span.use_point:
            return When(span.point, None, None, span.end, span.kind, f"in force on {fmt(span.point)}")
        return When(span.point, span.start, span.end, span.end, span.kind, f"in force during {period_label(span)}")
    for m in re.finditer(CONNECTIVE, q, re.I):
        if fixes and m.group(1).lower() == "when" and INTERROGATIVE.match(q, m.end()):
            continue  # "When did Nate win ...?" asks for a date; it names no anchor
        before = m.group(1).lower() == "before"
        tail = q[m.end():]
        near = " ".join(tail.split()[:8])
        for _, surface, refs in values.find(near):  # "when I got the iPhone 16"
            rows = [r for attr, item, nv in sorted(refs) for r in facts.believed(attr, item) if r.value and norm(r.value) == nv]
            if rows:
                r = min(rows, key=lambda r: r.valid_from)
                return _anchor(r, before, f"{m.group(1)} {surface}", keep_later=now_too and NOW_TOO.search(q) is not None)
        for k in sorted(vocab.route(" ".join(tail.split()[:4]))):  # "before I moved": that key's latest change
            rows = facts.believed(k)
            changes = [r for r in rows if rows and r.valid_from > rows[0].valid_from]
            if changes:
                return _anchor(max(changes, key=lambda r: r.valid_from), before, f"{m.group(1)} the {k} changed")
    return When(now, None, None, None, "now", f"in force now ({fmt(now)})")


def period_label(span: dates.Span) -> str:
    if span.precision == "month" and span.end == dates.add_months(span.start, 1):
        return f"{calendar.month_abbr[span.start.month]} {span.start.year}"
    if span.precision == "year":
        return str(span.start.year)
    return f"{fmt(span.start)} to {fmt(span.end - timedelta(days=1))}"


def _anchor(r: Row, before: bool, what: str, keep_later: bool = False) -> When:
    vf = date.fromisoformat(r.valid_from)
    as_of = vf - timedelta(days=1) if before else vf
    said = date.fromisoformat(r.recorded_at[:10])
    window = None if keep_later else as_of + timedelta(days=1) if before else max(vf, said) + timedelta(days=1)
    return When(as_of, None, None, window, "anchor", f"in force on {fmt(as_of)} ({what})")


def fact_line(r: Row, when: When, quote: str, said: str, confirmed: str | None) -> str:
    vf, vt = date.fromisoformat(r.valid_from), date.fromisoformat(r.valid_to)
    frm = {"on_or_before": "on or before ", "day": ""}.get(r.from_precision, "about ") + fmt(vf)
    name = r.attribute + (f" ({r.item})" if r.item else "")
    value = r.value if r.value is not None else "none, no longer has one"
    extra = (f"; last confirmed {fmt(date.fromisoformat(confirmed))}" if confirmed and vt >= FOREVER_DATE else "") \
        + ("; disputed" if r.status == "disputed" else "")
    return f'{name}: {value} (true from {frm} to {fmt(vt)}; {when.label}; said {fmt(date.fromisoformat(said))}: "{quote}"{extra})'


def fact_lines(mem, question: str, when: When, prefs: bool = False) -> tuple[list[str], list[Row]]:
    """Keyed lookup at the question's time (research/32 C), plus, for a preference or recommendation question with
    prefs on demand, every preference on record, said once or more (v2)."""
    facts, values, vocab = mem.facts, mem.values, mem.vocab
    keys = vocab.route(question) | {ref[0] for _, _, refs in values.find(question) for ref in refs}
    support: dict[int, tuple] = {}  # line index -> (row, quote, said, last confirmed), for the stale label
    shown, lines = [], []

    def add(r: Row, once: bool = False) -> None:
        d, src = mem.decision_by_id.get(r.decision_id, {}), r.source_event
        _, confirmed, newest = facts.stats_for(r.attribute, r.item, r.value)
        nd = mem.decision_by_id.get(newest or "")
        if nd and r.valid_to >= "9999-12-31" and nd["tx"] > r.recorded_at:
            d, src = nd, nd["source"]  # v3.2: quote the newest turn that states the value (an open row only)
        ev = mem.log.by_hash.get(src)
        quote = sentence_of(ev.text, d.get("value") or d.get("old")).strip()[:240] if ev else ""
        said = ev.ts[:10] if ev else r.recorded_at[:10]
        line = fact_line(r, when, quote, said, confirmed)
        lines.append(line[:-1] + "; said once)" if once else line)
        shown.append(r)
        support[len(lines) - 1] = (r, quote, said, confirmed)

    for k in sorted(keys):
        found = facts.at(k, (when.start or when.as_of).isoformat(), when.end.isoformat() if when.start else None)
        rows = [r for r in found if not hidden(facts, r)]
        once = not rows and bool(found)  # v5: a once-said row in force is shown, marked, not "nothing on record"
        rows = rows or found
        if not rows:
            stored = facts.believed(k)
            if stored:
                first = min(stored, key=lambda r: r.valid_from)
                lines.append(f"{k}: nothing on record {when.label.replace('in force ', '')}; the earliest record is "
                             f"{first.value or 'none'} from {fmt(date.fromisoformat(first.valid_from))}")
            continue
        for r in rows:
            add(r, once)
    if prefs:
        for r in facts.in_force(when.as_of.isoformat()):
            if (r.kind == "preference" or r.attribute in PREF_ATTRS) and r not in shown and r.value:
                add(r)
    mem._support = support
    return lines, shown


def stale(mem, lines: list[str], when: When, latest_day: str | None) -> list[str]:
    """Fix 5 (v2): a fact record said "in force now" although a later mention of its subject is shown (the gym at
    7 pm, when a later chat said 6 pm). Such a record says when it was last supported instead."""
    if when.how != "now" or not latest_day:
        return lines
    out = list(lines)
    for i, (r, quote, said, confirmed) in getattr(mem, "_support", {}).items():
        last = max(said, confirmed or "")
        if r.valid_to >= "9999-12-31" and latest_day > last:
            relabel = When(when.as_of, None, None, None, "now", f"last confirmed {fmt(date.fromisoformat(last))}; a later mention is shown above")
            out[i] = fact_line(r, relabel, quote, said, None)
    return out


BETWEEN = re.compile(r"\bbetween\s+(?:the\s+(?:day|time|date)\s+(?:when\s+|that\s+)?)?(.+?)\s+and\s+"
                     r"(?:the\s+(?:day|time|date)\s+(?:when\s+|that\s+)?)?(.+?)[?.!]*$", re.I)
REF = re.compile(r"^(.*?)\b(?:when|by the time|before|after)\s+(I\b.+?)[?.!]*$", re.I)
NUMBER_WORD = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def time_phrases(question: str, kind: str) -> list[str]:
    """The question's event phrases in frame order (research/36 B.3): "between A and B" -> [A, B]; "... X when I Y"
    -> [X, Y] (Y the reference); else the whole question. A rule stand-in for the model's match step."""
    m = BETWEEN.search(question)
    if m:
        return [m.group(1), m.group(2)]
    m = REF.search(question)
    if m and kind in ("duration", "order") and m.group(1).strip():
        return [m.group(1), m.group(2)]
    return [question]


def match_step(mem, question: str, kind: str) -> dict[str, list[list[str]]]:
    """research/36 B.3, the match step: question phrase -> the events it means, each event a list of its mentions
    (a same_as cluster). Model work in the design; here the $0 stand-in: stemmed BM25 of the phrase over the event
    clauses, the best event per phrase (an order question takes the best few). v2.1: an event must hold at least
    half of the phrase's idf-weighted terms (Search.coverage, STRONG); a weaker best match is no match."""
    s, store = mem.search, mem.events_store
    if s is None or not s.eunits or store is None:
        return {}
    n = next((v for w, v in NUMBER_WORD.items() if re.search(rf"\b{w}\b", question, re.I)), 6)
    groups = {}
    for ph in time_phrases(question, kind):
        roots, out = [], []
        for j in s._ebm25(ph):
            if s.coverage(ph, s.eunits[j]) < STRONG:
                continue
            root = store.root(s.eunits[j].eid)
            if root in roots:
                continue
            roots.append(root)
            if len(roots) >= (n if kind == "order" and len(groups) == 0 else 1):
                break
        for root in roots:
            out.append([r["event_id"] for r in store.rows("SELECT * FROM events WHERE event_id=? OR same_as=?", (root, root))])
        if out:
            groups[ph] = out
    return groups


def time_block(mem, question: str, now: date, rows: list) -> str | None:
    """The time module's hook (docs/timeline.md): needs_time, anchor_all over the matched events' items, then the
    rendered <time-computed> block, or None when the question is not about time or nothing matched."""
    kind = TIME.needs_time(question)
    if not kind or not mem.events_on:
        return None
    if kind == "point" and not TIME.POINT_Q.search(question):
        return None  # v2.1: a window in a count or lookup question ("how many ... in March") filters retrieval; no block
    groups = match_step(mem, question, kind) if kind != "true_at" else {}
    if kind != "true_at" and len(groups) < len(time_phrases(question, kind)):
        return None  # a phrase with no event on record: never compute from what is missing (today's path runs)
    if kind == "true_at" and not rows:
        return None  # no fact row for what it asks: "cannot be determined" would only say retrieval found no key
    by_id = {r["event_id"]: r for r in mem.events_store.rows()} if groups else {}
    need = {i for gs in groups.values() for g in gs for i in g}
    for i in list(need):  # what an event is measured from must be dated first
        ref = mem.decision_by_id.get(by_id[i]["decision_id"], {}).get("anchor_event") if i in by_id else None
        if ref and ref in by_id:
            need.add(ref)
    items = []
    for i in need:
        row, d = by_id[i], mem.decision_by_id.get(by_id[i]["decision_id"], {})
        clause = next((x["clause"] for x in mem.search.einfo if x["row"]["event_id"] == i), row["gist"])
        day = dates.day_of(row["said_at"])
        src = f'u{mem.log.by_hash[row["source_event"]].seq}, chat of {fmt(day)}: "{clause}"' if row["source_event"] in mem.log.by_hash else clause
        items.append(TIME.Item(i, clause, src, d.get("time"), d.get("tense"), extract_status(row["polarity"]), d.get("anchor_event"),
                               d.get("rel"), d.get("magnitude"), bool(d.get("just")), "user", day))
    done = TIME.anchor_all(items)
    anchors = {ph: [[done[i] for i in g if i in done] for g in gs] for ph, gs in groups.items()}
    r = TIME.timeline_for(question, now, anchors, rows=rows if kind == "true_at" else None)
    mem._last["time"] = TIME.trace(r)
    out = TIME.render(r) or None
    if out and r.status == "undetermined":  # v2.1: advisory, never a verdict the reader takes as a missing premise
        out = out.replace(TIME.INSTRUCTION, ADVISORY).replace(TIME.VETO, "")
        out = re.sub(r"\n{2,}", "\n", out)
    return out


ADVISORY = ("Advisory only, from the dated event records: code could not compute this from the events it matched. It is "
            "not a finding that the conversation lacks the answer; if the turns above state it, answer from them.")


def extract_status(polarity: str) -> str:
    from .extract import STATUS

    return STATUS[polarity]


def anchor_event(mem, tail: str) -> date | None:
    """The date an event named in a question's tail happened ("since I started painting classes"): the best event
    unit by stemmed BM25 over its clause, its happened date, else the day it was said."""
    s = mem.search
    ranked = s._ebm25(tail) if s is not None and s.eunits else []
    for j in ranked[:1]:
        row = s.einfo[j]["row"]
        return date.fromisoformat(row["occurred_from"] or row["said_at"][:10])
    return None


def event_lists(mem, question: str) -> list[tuple[list, float]]:
    """research/37 E3: direct joins (events whose class or entity the question names; done, not planned) and, when
    the question names one, spreading activation from those cues. (ranked keys, RRF weight) for Search.search."""
    s = mem.search
    if not mem.events_on or s is None or not s.eunits:
        return []
    ents, cls = s.entities_in(question), mem.classes.classes_in(question, "object")  # actions are relations, not cues
    if not ents and not cls:
        return []
    score = lambda j: 2 * len(s.einfo[j]["ents"] & ents) + len(s.einfo[j]["classes"] & cls)
    direct = [j for j in range(len(s.eunits)) if s.einfo[j]["row"]["polarity"] == "did" and score(j)]
    direct.sort(key=lambda j: (-score(j), -s.eunits[j].seq))
    out = [([("e", j) for j in direct[:mem.event_cap]], W_DIRECT)]
    if mem.activation and getattr(mem, "graph", None) is not None:
        dense = s._edense(question, 20) if mem.vectors and s.embed_model else []
        out.append((mem.graph.query(ents, cls, dense, mem.activation_cap), W_PPR))
    mem._last_seeds = (ents, cls)
    return out


def occurrence_label(row: dict) -> str:
    a, b, prec = row["occurred_from"], row["occurred_to"], row["occ_precision"]
    if prec == "on_or_before" or not a:
        return f"on or before {fmt(date.fromisoformat(b) - timedelta(days=1))}" if b else "date unknown"
    start = date.fromisoformat(a)
    if prec == "day":
        return fmt(start)
    if prec == "week":
        return f"week of {fmt(start)}"
    if prec == "month":
        return f"{calendar.month_abbr[start.month]} {start.year}"
    if prec == "year":
        return str(start.year)
    return f"{fmt(start)} to {fmt(date.fromisoformat(b) - timedelta(days=1))}"


POLARITY_NOTE = {"plan": "planned, not done: ", "hypothetical": "maybe, not done: ", "negated": "did NOT happen: ", "did": ""}


def timeline_lines(mem, hits: list[Unit], question: str = "") -> list[str]:
    """research/37 E4: the event units among the hits, one line per real event (same_as mentions folded into their
    first), in order of when they HAPPENED, each with its class words beside the user's verbatim clause. A line is
    shown only when it meets the question literally (an entity, a class or a stemmed content word in common): the
    reader is handed a literal match (research/37 G1), and look-alike events reached by meaning alone stay out."""
    s, store = mem.search, mem.events_store
    info = {s.einfo[j]["row"]["event_id"]: s.einfo[j] for j in range(len(s.eunits))}
    q_ents, q_cls, q_terms = s.entities_in(question), mem.classes.classes_in(question, "object"), set(s.terms(question))
    reach = set(q_ents)  # the typed path of activation: entities of the events that name a question entity
    for i in s.einfo:
        if i["ents"] & q_ents:
            reach |= i["ents"]
    meets = lambda i: bool(i["ents"] & reach or i["classes"] & q_cls or q_terms & set(s.terms(i["clause"])))
    roots: dict[str, list[dict]] = {}
    for u in hits[:TIMELINE_HITS]:  # a short digest of the best hits (research/38 B), not every event in the pool
        if u.kind == "event" and u.eid in info and (not question or meets(info[u.eid])):
            root = store.root(u.eid)
            roots.setdefault(root, [])
            if info[u.eid] not in roots[root]:
                roots[root].append(info[u.eid])
            if len(roots) >= mem.event_cap:
                break
    rows = []
    for root, mentions in roots.items():
        first = info.get(root) or mentions[0]
        rows.append((first["row"]["occurred_from"] or first["row"]["occurred_to"] or "", first, mentions))
    out = []
    for n, (_, first, mentions) in enumerate(sorted(rows, key=lambda x: x[0]), 1):
        row = first["row"]
        cue = "; ".join(c.replace("_", " ") for c in (row["action_class"], row["object_class"]) if c)
        said = fmt(date.fromisoformat(row["said_at"][:10]))
        again = sorted({fmt(date.fromisoformat(m["row"]["said_at"][:10])) for m in mentions if m is not first} - {said})
        more = f"; mentioned again {', '.join(again)}" if again else ""
        out.append(f'E{n} {occurrence_label(row)} [{cue}] {POLARITY_NOTE[row["polarity"]]}"{first["clause"]}" (said {said}{more})')
    return out


def recall(mem, question: str, ts: str) -> tuple[str, list[Row], When]:
    """The recalled block, the fact rows it shows (so the caller can count their use), and the time it read.
    Leaves mem._last for followup(): what was found, so a later step never searches twice."""
    now = dates.day_of(ts)
    anchor = (lambda tail: anchor_event(mem, tail)) if mem.events_on else None
    when = date_in(question, now, mem.facts, mem.values, mem.vocab, mem.date_fixes, mem.anchor_now, mem.since_range, anchor)
    cats = categories(question)
    lines, shown = fact_lines(mem, question, when, prefs=mem.prefs_on_demand and "preference" in cats)
    window = when.window.isoformat() if when.window else None
    start = when.start.isoformat() if when.start and mem.events_on else None  # events: filter on when it HAPPENED
    event_end = when.end.isoformat() if when.start and when.end and mem.events_on else None
    more = event_lists(mem, question)
    agg = mem.aggregate and "aggregate" in cats
    k, budget = (round(mem.k * mem.agg_k), max(mem.budget, mem.agg_budget)) if agg else (mem.k, mem.budget)
    budget = min(budget, int(getattr(mem, "_room", budget * READER_FACTOR) / READER_FACTOR))  # v3.1: cap in reader tokens
    ev = "timespan" in cats  # v3.1: event dates are shown only when the question is about time
    trim = mem.agg_trim if agg or (mem.trim_all and not ASKS_ASSISTANT.search(question)) else 0
    prefer = (lambda u: NUMBER_BOOST if QUANTITY.search(u.text) else 1.0) if agg else None
    extra = mem.search.entities.expand(question) if mem.search and mem.search.entities else []
    rewrites = rewrites_for(mem, question, cats)
    search = (lambda q, x: mem.search.search(q, window, max(100, 2 * k), prefer, x, mem.vectors, start, more, event_end)) \
        if mem.search else None
    found = search(question, extra) if search else []
    fused = (found if agg and mem.agg_nodiv else diversify(found, mem.per_key))[:k]  # fix 2: no cap when summing
    if when.day:  # fix 4: the named day's own turns first (said that day, or naming it)
        fused = [u for u in fused if u.day == when.day.isoformat() or u.event == when.day.isoformat()] + \
            [u for u in fused if not (u.day == when.day.isoformat() or u.event == when.day.isoformat())]
    pool, scores = fused, {}
    if search and rewrites:  # rewrites only add candidates below the original's (research guard, Mohammed 13 Oct)
        more = [u for u in search(question, extra + [(q, 0.5) for q in rewrites]) if u not in fused]
        pool = fused + more[:20]
    hits = fused
    if mem.rerank and pool:  # the cross-encoder scores every candidate against the ORIGINAL question
        from . import vectors

        cand = pool[:mem.rerank_pool] + pool[len(fused):]
        sc = vectors.rerank(question, [u.text[:2000] for u in cand], mem.rerank_model)
        scores = {u.seq: x for u, x in zip(cand, sc, strict=True)}
        ranked = sorted(cand, key=lambda u: -scores[u.seq])
        # "top": the best rerank_k units, each with its neighbours; "order": every hit, in the reranker's order
        hits = ranked[:round(mem.rerank_k * (mem.agg_k if agg else 1))] if mem.rerank_mode == "top" else ranked
    mem._last = {"question": question, "when": when, "cats": cats, "agg": agg, "ev": "timespan" in cats, "fused": fused, "hits": hits, "scores": scores,
                 "lines": lines, "items": None, "op": None, "sessions": [], "done": set(), "checked": False, "budget": budget,
                 "strong": bool(lines) or any(mem.search.coverage(question, u) >= STRONG for u in hits[:20]) if mem.search else bool(lines)}
    if not lines and not hits:
        return ABSTAIN, [], when
    head = []
    if mem.mapreduce and agg and search:
        mr = run_mapreduce(mem, question, fused, now)
        if mr:
            head = [mr]
    # the time module (timeline.py, another builder) computes <time-computed> from its own Anchors; mapping EVENTS rows
    # and question phrases onto them is the joint step still to do, so the computed block is not wired here yet
    tl = timeline_lines(mem, hits, question) if mem.events_on and mem.timeline and mem.search and mem.search.eunits else []
    tl = ["<timeline>"] + tl + ["</timeline>"] if tl else []
    tb = time_block(mem, question, now, shown) if mem.time else None  # counted first, never trimmed, shown last
    budget -= sum(n_tokens(x) + 1 for x in lines + head + tl + ([tb] if tb else []))  # never trimmed
    ev = False if tb else ev  # no turn-level date brackets when the time module's block is on
    chosen = pick(mem.search, hits, budget, mem.neighbours, mem.session_cap,
                  window_first=agg or (bool(scores) and mem.rerank_mode == "top"), trim=trim, ev=ev) if hits else {}
    shown_blocks, latest_day = blocks(mem, question, hits, chosen, trim, with_latest=True, ev=ev)
    if mem.stale_label:
        lines = stale(mem, lines, when, latest_day)
    body = head + tl + shown_blocks + lines + ([tb] if tb else [])  # the computed block last, next to the question
    mem._last["digest"] = digest(mem, question, hits, lines, tl)
    guide = [GUIDE] if mem.guide and (lines or any(mem.search.coverage(question, u) >= STRONG for u in hits)) else []
    if not body:  # hits, but none fitted the budget
        return ABSTAIN, [], when
    return f'<recalled-data {when.how}="{when.label}">\n' + "\n".join(guide + body) + "\n</recalled-data>", shown, when


DIGEST_LINES, DIGEST_CHARS = 5, 1200


def digest(mem, question: str, hits: list[Unit], lines: list[str], tl: list[str]) -> str:
    """The short evidence digest the runner shows next to the question and in its one re-ask (research/38 B):
    verbatim lines only. The fact records first, then the timeline's event lines, then the sentence of each best hit
    that holds most of the question's terms, dated; at most DIGEST_LINES lines and DIGEST_CHARS characters."""
    out = list(lines[:2]) + [x for x in tl if re.match(r"E\d+ ", x)][:2]
    q = set(mem.search.terms(question)) if mem.search else set()
    for u in hits:
        if len(out) >= DIGEST_LINES or not mem.search:
            break
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", u.text.split(" / ")[0]) if len(s.strip()) > 20]
        best = max(sents, key=lambda s: len(q & set(mem.search.terms(s))), default="")
        if best and len(q & set(mem.search.terms(best))) >= 2 and not any(best[:60] in x for x in out):
            out.append(f'[{fmt(date.fromisoformat(u.day))}] "{best[:300]}"')
    text = "\n".join(out)
    return text[:DIGEST_CHARS]


def blocks(mem, question: str, hits: list[Unit], chosen: dict[str, set[int]], trim: int, with_latest: bool = False, ev: bool = True):
    """Chosen turns as dated session blocks in time order; the latest of several on-subject sessions labelled.
    with_latest: also return that latest session's date (or None)."""
    order = sorted((sid for sid in chosen if chosen[sid]), key=lambda sid: mem.search.sessions[sid][0].seq)
    strong = {u.session for u in hits if u.lo in chosen.get(u.session, ()) and mem.search.coverage(question, u) >= STRONG}
    latest = max(strong, key=order.index) if len(strong) >= 2 else None
    label = {sid: "latest mention" if sid == latest else "earlier mention" for sid in strong} if latest else {}
    out = [mem.search.render(sid, chosen[sid], label.get(sid, ""), trim, ev) for sid in order]
    if with_latest:
        return out, (mem.search.sessions[latest][0].ts[:10] if latest else None)
    return out


def rewrites_for(mem, question: str, cats: set[str]) -> list[str]:
    if mem.rewrite == "off" or (mem.rewrite == "complex" and not cats):
        return []
    from .rewrite import rewrite
    from .triage import call

    return rewrite(call, mem.model, question)


def matched_sessions(mem, fused: list[Unit]) -> list[str]:
    out = []
    for u in fused:
        if u.session not in out:
            out.append(u.session)
    return out[:mem.mr_sessions]


def run_mapreduce(mem, question: str, fused: list[Unit], today: date) -> str:
    """Map (one cached writer call per matched chat), combine in code; "" when nothing parseable came back."""
    from .triage import call

    sessions = matched_sessions(mem, fused)
    spent = {"tokens": 0}

    def metered(model, messages, **p):  # v3: writer calls count against the per-question cap; stop before it
        if mem._last.get("writer_tokens", 0) + spent["tokens"] + sum(n_tokens(m["content"]) for m in messages) > mem.token_cap // 2:
            raise ValueError("token cap")
        r = call(model, messages, **p)
        spent["tokens"] += r.prompt_tokens + r.completion_tokens
        return r
    items, _, _ = mapreduce.extract(metered, mem.model, mem.search, question, sessions, mem.agg_trim)
    mem._last["writer_tokens"] = mem._last.get("writer_tokens", 0) + spent["tokens"]
    op = mapreduce.operation(question)
    mem._last.update(items=items, op=op, sessions=sessions)
    if not items:
        return ""
    return mapreduce.render(op, mapreduce.combine(op, items, today), items)


def rung(mem, n: int, question: str, ts: str) -> str | None:
    """The escalation ladder after an exact "I don't know" (Mohammed 13 Oct). 1: the best 8 reranked units with
    neighbours; 2: the top-scoring chat in full plus the fact records; 3: map-reduce over every matched chat.
    "" skips a rung that would repeat what the reader already had; None: no rung n."""
    last = mem._last
    if not last or not mem.search:
        return None
    when, lines = last["when"], last["lines"]
    wrap = lambda body: f'<recalled-data {when.how}="{when.label}">\n' + "\n".join(body) + "\n</recalled-data>"
    left = mem.token_cap - last.get("spent", 0) - 1000  # v3: what the per-question cap leaves for this context
    if left < 500:
        return ""
    if n == 1:
        if mem.rerank_mode == "top" or not last["fused"] or not mem.rerank:
            return ""  # the first answer already read the best reranked units (or there is no reranker)
        from . import vectors

        pool = last["fused"][:mem.rerank_pool]
        sc = vectors.rerank(question, [u.text[:2000] for u in pool], mem.rerank_model)
        top = [u for _, u in sorted(zip(sc, pool, strict=True), key=lambda x: -x[0])][:mem.rerank_k]
        chosen = pick(mem.search, top, min(last["budget"], int(left / READER_FACTOR)), mem.neighbours, mem.session_cap,
                      window_first=True, ev=last["ev"])
        return wrap(blocks(mem, question, top, chosen, 0, ev=last["ev"]) + lines)
    if n == 2:
        ranked = last["hits"] or last["fused"]
        if not ranked:
            return ""
        sid = ranked[0].session
        turns = list(range(len(mem.search.sessions[sid])))
        cap, kept = min(RUNG2_TOKENS, int(left / READER_FACTOR)), []  # v3: never a whole chat beyond the per-question cap
        for i in turns:  # the whole chat, inside a reader-window-safe cap
            cap -= mem.search.cost(sid, i)
            if cap < 0:
                break
            kept.append(i)
        return wrap([mem.search.render(sid, set(kept), "the chat that matched best, in full")] + lines)
    if n == 3:
        if last["items"] is not None:
            return ""  # map-reduce already shaped the first answer
        mr = run_mapreduce(mem, question, last["fused"], dates.day_of(ts))
        return wrap([mr] + lines) if mr else ""
    return None


def check(mem, question: str, answer: str, ts: str) -> str | None:
    """Self-check on an aggregation answer (Mohammed 13 Oct): one cached call compares the answer with the extracted
    items and may add missing ones (verbatim) or drop wrong ones; code recomputes. A new context only if the
    computed result changed."""
    last = mem._last
    if not last or not last["agg"] or not last["items"] or last["checked"]:
        return None
    last["checked"] = True
    from .triage import call

    today = dates.day_of(ts)
    before = mapreduce.combine(last["op"], last["items"], today)
    items, changed = mapreduce.selfcheck(call, mem.model, mem.search, question, answer, last["items"], last["sessions"], mem.agg_trim)
    after = mapreduce.combine(last["op"], items, today)
    if not changed or after == before:
        return None
    last["items"] = items
    when = last["when"]
    return (f'<recalled-data {when.how}="{when.label}">\n' + mapreduce.render(last["op"], after, items)
            + "\n" + "\n".join(last["lines"]) + "\n</recalled-data>")


def pick(search: Search, hits: list[Unit], budget: int, neighbours: int, cap: int, min_hits: int = 3,
         window_first: bool = False, trim: int = 0, ev: bool = True) -> dict[str, set[int]]:
    """Which turns to show, inside the token budget; overlapping windows merge (a turn is shown and paid for once).

    Default (v1.1): coverage first, every hit's own unit in rank order while it fits; then context with what is
    left, in rank order: a session with `min_hits` or more hits brings its turns around them (at most `cap`), any
    other hit its `neighbours` turns either side (a block that does not fit shrinks to the window).

    window_first (aggregation questions, v1.3): each hit with its neighbours, in rank order, before the next hit
    (a window that does not fit shrinks to the bare unit). Measured at $0 on LongMemEval: of the orders tried, it
    is the one where every answer turn shown also has its neighbours (70 of 94; v1.2's one-unit-per-session
    order: 24); v1.2's order showed sessions, but rarely the turn in them that held the number."""
    chosen: dict[str, set[int]] = {}

    def take(session: str, want: set[int]) -> bool:
        nonlocal budget
        have = chosen.setdefault(session, set())
        cost = sum(search.cost(session, i, trim, ev) for i in want - have)
        if cost > budget:
            return False
        have |= want
        budget -= cost
        return True

    def window(u: Unit) -> set[int]:
        return set(range(max(0, u.lo - neighbours), min(len(search.sessions[u.session]), u.hi + neighbours + 1)))

    if window_first:
        for u in hits:
            take(u.session, window(u)) or take(u.session, set(range(u.lo, u.hi + 1)))
        return chosen
    for u in hits:
        take(u.session, set(range(u.lo, u.hi + 1)))
    per_session: dict[str, list[Unit]] = {}
    for u in hits:
        if u.lo in chosen.get(u.session, ()):
            per_session.setdefault(u.session, []).append(u)
    blocked = set()
    for u in (u for u in hits if u.lo in chosen.get(u.session, ())):
        if len(per_session[u.session]) >= min_hits and u.session not in blocked:
            blocked.add(u.session)
            if take(u.session, search.around(u.session, per_session[u.session], cap)):
                continue
        take(u.session, window(u))
    for s, have in chosen.items():  # v4: a lone gap between two shown turns is shown (it ties them: "it" = the snake)
        for i in sorted(have):
            if i + 2 in have and i + 1 not in have:
                take(s, {i + 1})
    return chosen


def diversify(units: list[Unit], per_key: int) -> list[Unit]:
    """No key hogs the budget: a unit whose keys all already have per_key units goes to the back."""
    seen: dict[str, int] = {}
    front, back = [], []
    for u in units:
        if u.keys and all(seen.get(k, 0) >= per_key for k in u.keys):
            back.append(u)
            continue
        for k in u.keys:
            seen[k] = seen.get(k, 0) + 1
        front.append(u)
    return front + back
