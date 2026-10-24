"""Time in code: research/36 B.2 to B.6 and C (memory algorithm v2, research/39). The model never dates, subtracts or
orders anything here. The input is the extractor's checked items (verbatim spans and closed labels, 36 B.2), the
output is the <time-computed> block the reader phrases, or "cannot be determined" with the missing piece named.

    anchor(x, chat_day, done)      one item -> an Anchor: closed-open day bounds, nominal day, granule, basis
    merge(mentions)                one event said several times: bounds intersected, a conflict flagged
    allen(p, q), relation(p, q)    Allen's relations where the bounds decide them, else "undetermined"
    elapsed(p, q, unit)            q minus p in the unit asked; a range when an input is coarser than the unit
    true_at(rows, t)               FACTS rows in force during t (kind 5)
    needs_time(question)           point | duration | order | since | true_at | None, by rules
    timeline_for(question, as_of, anchors, rows=None) -> TimelineResult;  render(result) -> str
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import partial

from . import dates
from .complex_triggers import categories
from .dates import GRANS, fmt

DAY = timedelta(days=1)
STATUSES = ("happened", "ongoing", "planned", "hypothetical", "habitual", "negated")
TENSES = ("past", "present", "future")
RELS = ("on", "before", "after", "since", "until", "for", "in_advance")
BASES = ("explicit", "relative", "for_now", "mentioned", "unknown")
RANK = {"explicit": 0, "relative": 1, "for_now": 2, "mentioned": 3}  # which mention wins a conflict (36 B.2)
UNIT_GRAN = {"day": "day", "week": "week", "month": "month", "year": "year", "age": "year"}
INSTRUCTION = "Computed by code from the dated lines below. Phrase this result in your answer; do not compute it again."
VETO = "If an event listed here is not the one the question means, say so and give no number."
KIND_LABEL = {"point": "what happened at a time", "duration": "time between two events", "since": "time since an event",
              "order": "order of events", "true_at": "what was true at a time"}


@dataclass(frozen=True)
class Item:
    """One checked extractor item (36 B.2): verbatim spans and closed labels only, never a date or a number."""
    id: str
    event: str  # the shortest verbatim span naming the event
    src: str  # where it was said, shown as is: 'u201, chat of 5 Jan 2023: "..."'
    time: str | None = None  # verbatim time words that date THIS event
    tense: str | None = None  # past | present | future
    status: str = "happened"  # happened | ongoing | planned | hypothetical | habitual | negated
    anchor_id: str | None = None  # the item this one is measured from
    rel: str | None = None  # on | before | after | since | until | for | in_advance
    magnitude: str | None = None  # verbatim amount for "N before / after / in advance"
    just: bool = False  # "just", "today", "now" put it on the chat's day
    speaker: str = "user"  # only the user's own turns date the user's events (36 F, rule 9)
    day: date | None = None  # the chat's date (anchor_all reads it)


@dataclass(frozen=True)
class Anchor:
    """One event, dated by code. A point event occupies one day somewhere in [lo, hi); None is open on that side."""
    eid: str
    words: str
    src: str
    lo: date | None
    hi: date | None
    nominal: date | None  # the day, if the amounts the user gave are exact
    gran: str  # day | week | month | season | year
    how: str  # the basis: explicit | relative | for_now | mentioned | unknown
    why: str = ""  # how it was dated, or why not
    approx: bool = False  # dated through a vague count ("a few"): its nominal is a convention
    readings: tuple = ()  # two readings kept, as Anchors
    plan: Anchor | None = None  # a planned event: the day it was planned for (never used as its date)
    conflict: bool = False  # mentions that cannot all be true; the strongest basis won

    @property
    def precision(self) -> str:
        """The granule as shown, with on_or_before for an event dated only by its mention (the EVENTS occ_precision)."""
        return "on_or_before" if self.lo is None and self.hi is not None else self.gran or "none"


@dataclass(frozen=True)
class Elapsed:
    unit: str  # day | week | month | year | age
    value: int | None  # the one number: bounds collapse, or every input is no coarser than the unit
    least: int | None  # the smallest and largest possible; None = unbounded on that side
    most: int | None
    nominal: int | None  # the number if the amounts said are exact
    frm: Anchor
    to: Anchor


@dataclass
class TimelineResult:
    kind: str | None  # point | duration | since | order | true_at; None: not a time question
    question: str
    as_of: date
    status: str  # ok | range | two_readings | undetermined | skip
    answer: list[str]  # the computed lines, as the reader sees them
    timeline: list[Anchor]  # merged, oldest first, unknowns last
    value: dict = field(default_factory=dict)  # the result as data (tests, traces, the replay)
    missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------- anchoring (B.2)

def check(x: Item, turn: str) -> str | None:
    """Why code drops an item before use (36 B.2), or None: a span not verbatim in its turn, a label off its list."""
    for name in ("event", "time", "magnitude"):
        v = getattr(x, name)
        if v and v.lower() not in turn.lower():
            return f"{name} not verbatim"
    if x.status not in STATUSES:
        return f"status {x.status!r}"
    if x.tense not in (None, *TENSES):
        return f"tense {x.tense!r}"
    return f"rel {x.rel!r}" if x.rel not in (None, *RELS) else None


def fwd(d: date, amount: tuple) -> date:
    return dates.add_months(d, amount[0]) + timedelta(days=amount[1])


def coarser(a: str, b: str) -> str:
    return max(a, b, key=GRANS.index)


def _clamp(a: Anchor, chat_day: date) -> Anchor | None:
    """An occurrence cannot lie after the day it was told; None when nothing is left."""
    hi = min(a.hi, chat_day + DAY) if a.hi else chat_day + DAY
    if a.lo and a.lo >= hi:
        return None
    nominal = a.nominal if a.nominal and a.nominal < hi else None
    return replace(a, hi=hi, nominal=nominal, gran="day" if a.lo and (hi - a.lo).days == 1 else a.gran)


def anchor(x: Item, chat_day: date, done: dict[str, Anchor]) -> Anchor:
    """research/36 C.1: one checked item, dated. Only occurrences get dates; only the user's own turns date them."""
    a = partial(Anchor, x.id, x.event, x.src)
    said = f"said {fmt(chat_day)}"

    def unknown(why: str, plan: Anchor | None = None) -> Anchor:
        return a(None, None, None, "", "unknown", why, plan=plan)

    if x.speaker != "user":
        return unknown(f"an {x.speaker} line; only the user's own turns date the user's events")
    if x.rel == "until":
        return unknown('"until" bounds the end of a state; it dates no occurrence')
    if x.status not in ("happened", "ongoing"):
        plan = None
        t = dates.timex(x.time, chat_day, x.tense or "future") if x.time and x.status == "planned" else None
        if t and t.status == "ok" and t.type in ("DATE", "TIME"):
            plan = a(t.lo, t.hi, t.nominal, t.gran, "explicit" if t.absolute else "relative", f'planned for "{x.time}", {said}')
        return unknown(f"{x.status}, not an occurrence; only occurrences get dates", plan)
    t = dates.timex(x.time, chat_day, x.tense) if x.time else None
    vague = ""
    if x.time:
        if t is None or t.status == "refused":
            return unknown(f'the time words "{x.time}" are not understood' + (f" ({t.note})" if t else ""))
        if t.type in ("SET", "NAME"):
            return unknown(f'"{t.text}" is {"a recurring time" if t.type == "SET" else "a date inside a name"}; it dates no occurrence')
        if t.status == "vague":
            vague = f' ("{x.time}" is vague)'
        elif t.type == "DURATION":
            if not (t.to_now or x.status == "ongoing" or x.tense == "present") or t.amount is None and t.longest is None:
                return unknown(f'"{x.time}" is an amount of time with no end point')
            lo = dates.back(chat_day, t.longest) if t.longest else None
            nominal = dates.back(chat_day, t.amount) if t.amount else None
            what = "born then" if t.age else "it began then"
            return a(lo, dates.back(chat_day, t.shortest) + DAY, nominal, t.gran, "for_now", f'"{x.time}", {said}: {what}'
                     + (f"; {t.note}" if t.note else ""), approx=t.note == dates.VAGUE_NOTE)
        else:
            how = "explicit" if t.absolute else "relative"
            note = f"; {t.note}" if t.note and t.status == "ok" else ""
            if t.status == "two_readings":
                rs = [r for r in (_clamp(a(r.lo, r.hi, r.nominal, r.gran, how, f'"{x.time}" read as {when_text(r)}, {said}'),
                                         chat_day) for r in t.readings) if r]
                if len(rs) == 1:
                    return replace(rs[0], why=rs[0].why + " (the other reading is after the day it was said)")
                if not rs:
                    return unknown(f'"{x.time}" names a day after the day it was said')
                return a(min(r.lo for r in rs), max(r.hi for r in rs), None, max((r.gran for r in rs), key=GRANS.index), how,
                         f'"{x.time}", {said}: two readings', readings=tuple(rs))
            got = _clamp(a(t.lo, t.hi, t.nominal, t.gran, how, f'"{x.time}", {said}{note}', approx=t.note == dates.VAGUE_NOTE),
                         chat_day)
            return got or unknown(f'"{x.time}" names a day after the day it was said')
    if x.anchor_id:
        b = done.get(x.anchor_id)
        if b is None or b.how == "unknown":
            return unknown(f"measured from an event with no date on record ({x.anchor_id})")
        if not x.magnitude:  # on the day of the other event
            return replace(b, eid=x.id, words=x.event, src=x.src, why=f"{x.rel or 'on'} {b.words}: {b.why}", readings=())
        m = dates.amount(x.magnitude)
        if m is None or m.amount is None:
            return unknown(f'"{x.magnitude}" is not an amount of time')
        near, far = m.shortest, m.longest or m.amount
        if x.rel in ("before", "in_advance"):
            lo, hi, nominal = (dates.back(b.lo, far) if b.lo else None), (dates.back(b.hi, near) if b.hi else None), \
                (dates.back(b.nominal, m.amount) if b.nominal else None)
        else:
            lo, hi, nominal = (fwd(b.lo, near) if b.lo else None), (fwd(b.hi, far) if b.hi else None), \
                (fwd(b.nominal, m.amount) if b.nominal else None)
        got = _clamp(a(lo, hi, nominal, coarser(b.gran, m.gran), "relative",
                       f'"{x.magnitude}" {x.rel or "after"} "{b.words}" ({when_text(b)})', approx=b.approx), chat_day)
        return got or unknown(f'"{x.magnitude}" {x.rel} the other event lands after the day it was said')
    if x.just:
        return a(chat_day, chat_day + DAY, chat_day, "day", "mentioned", f"said to be just done; dated by the chat of {fmt(chat_day)}")
    return a(None, chat_day + DAY, chat_day, "day", "mentioned",
             f"no date given{vague}; dated by the chat it was said in ({fmt(chat_day)}), so on or before that day")


def anchor_all(items) -> dict[str, Anchor]:
    """Every item anchored in dependency order (an event measured from another comes after it); a cycle is unknown."""
    by, done = {x.id: x for x in items}, {}

    def visit(x: Item, seen: frozenset) -> None:
        if x.id in done:
            return
        dep = by.get(x.anchor_id) if x.anchor_id else None
        if dep is not None and dep.id not in done:
            if dep.id in seen:
                done[x.id] = Anchor(x.id, x.event, x.src, None, None, None, "", "unknown", "measured in a cycle of events")
                return
            visit(dep, seen | {x.id})
        if x.id not in done:
            done[x.id] = anchor(x, x.day, done)

    for x in items:
        visit(x, frozenset())
    return done


def merge(mentions) -> Anchor:
    """One event said several times: intersect the bounds. Empty: the strongest basis wins (explicit, relative,
    for-now, mentioned) and the conflict is flagged (36 B.2)."""
    mentions = list(mentions)
    known = sorted((m for m in mentions if m.how != "unknown"), key=lambda m: RANK[m.how])
    if not known:
        return mentions[0]
    best = known[0]
    if len(mentions) == 1:
        return best
    srcs = "; ".join(dict.fromkeys(m.src for m in [best, *mentions]))
    lo = max((m.lo for m in known if m.lo), default=None)
    hi = min((m.hi for m in known if m.hi), default=None)
    if lo and hi and lo >= hi:
        rest = ", ".join(f"{m.how} {when_text(m)}" for m in known[1:])
        return replace(best, src=srcs, conflict=True, why=f"{best.why}; conflicts with another mention ({rest}); {best.how} wins")
    readings = tuple(r for r in best.readings if (not lo or not r.hi or lo < r.hi) and (not hi or not r.lo or r.lo < hi))
    if best.readings and len(readings) == 1:
        best, readings = readings[0], ()
    nominal = best.nominal if best.nominal and (not lo or lo <= best.nominal) and (not hi or best.nominal < hi) else None
    one_day = lo and hi and (hi - lo).days == 1
    return replace(best, src=srcs, lo=lo, hi=hi, nominal=lo if one_day else nominal, gran="day" if one_day else best.gran,
                   readings=readings, why=f"{best.why}; {len(mentions) - 1} more mention(s) agree")


# ------------------------------------------------------------------------------------- relations and arithmetic (B.4)

INF = float("inf")
# Allen's 13 relations as the signs of (a1-b1, a1-b2, a2-b1, a2-b2), a = [a1, a2), b = [b1, b2) [Allen p834 Fig. 1]
ALLEN = {"before": "<<<<", "meets": "<<=<", "overlaps": "<<><", "finished_by": "<<>=", "contains": "<<>>", "starts": "=<><",
         "equals": "=<>=", "started_by": "=<>>", "during": "><><", "finishes": "><>=", "overlapped_by": "><>>",
         "met_by": ">=>>", "after": ">>>>"}


def _day(d: date | None, dflt: float, k: int = 0) -> float:
    return d.toordinal() + k if d else dflt


def _ends(x) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where an event's start day and its (exclusive) end can lie, as day numbers. An Anchor is a one-day event;
    a (start, end) pair of Anchors is an event with extent."""
    s, e = (x, x) if isinstance(x, Anchor) else x
    return (_day(s.lo, -INF), _day(s.hi, INF, -1)), (_day(e.lo, -INF, 1), _day(e.hi, INF))


def _signs(x: tuple, y: tuple) -> set[str]:
    lo, hi = x[0] - y[1], x[1] - y[0]
    return {s for s, ok in (("<", lo < 0), ("=", lo <= 0 <= hi), (">", hi > 0)) if ok}


def possible(p, q) -> set[str]:
    """Allen's relations p can have to q given the bounds; a set when they allow several [Allen p835 Fig. 3]. Two one-day
    events go by the difference of their days (exact); events with extent by the endpoint signs the bounds allow."""
    if isinstance(p, Anchor) and isinstance(q, Anchor):
        lo, hi = _day(p.lo, -INF) - _day(q.hi, INF, -1), _day(p.hi, INF, -1) - _day(q.lo, -INF)
        return {r for r, (a, b) in (("before", (-INF, -2)), ("meets", (-1, -1)), ("equals", (0, 0)), ("met_by", (1, 1)),
                                    ("after", (2, INF))) if lo <= b and a <= hi}
    (a1, a2), (b1, b2) = _ends(p), _ends(q)
    signs = (_signs(a1, b1), _signs(a1, b2), _signs(a2, b1), _signs(a2, b2))
    return {r for r, code in ALLEN.items() if all(c in got for c, got in zip(code, signs))}


def allen(p, q) -> str:
    """The one Allen relation the bounds decide, or "undetermined". Dates first, no transitivity table [Allen p842]."""
    got = possible(p, q)
    return got.pop() if len(got) == 1 else "undetermined"


def relation(p, q) -> str:
    """For ordering: before (incl. meets), after (incl. met-by), same_day, or undetermined. Before is certain only when
    p.hi <= q.lo (36 B.4)."""
    got = possible(p, q)
    if got <= {"before", "meets"}:
        return "before"
    if got <= {"after", "met_by"}:
        return "after"
    return "same_day" if got == {"equals"} else "undetermined"


def count(p: date, q: date, unit: str) -> int:
    """q minus p. Days and weeks count days; months and years subtract at that field, never through days
    [Snodgrass p34, p39-40]; an age counts completed years."""
    if unit in ("day", "week"):
        return (q - p).days
    if unit == "month":
        return (q.year - p.year) * 12 + q.month - p.month
    if unit == "year":
        return q.year - p.year
    return q.year - p.year - ((q.month, q.day) < (p.month, p.day))


def elapsed(p: Anchor, q: Anchor, unit: str) -> Elapsed:
    """q minus p in the unit asked. The extremes come from the bounds; one number only when they collapse or every input
    is no coarser than the unit (and not a vague count); otherwise a range with the nominal labelled (36 B.4)."""
    least = count(p.hi - DAY, q.lo, unit) if p.hi and q.lo else None
    most = count(p.lo, q.hi - DAY, unit) if p.lo and q.hi else None
    nominal = count(p.nominal, q.nominal, unit) if p.nominal and q.nominal else None
    value = least if least is not None and least == most else nominal if _fine(p, q, unit) else None
    return Elapsed(unit, value, least, most, nominal, p, q)


def _fine(p: Anchor, q: Anchor, unit: str) -> bool:
    return all(GRANS.index(x.gran) <= GRANS.index(UNIT_GRAN[unit]) and not x.approx for x in (p, q))


def either_way(p: Anchor, q: Anchor, unit: str) -> Elapsed:
    """The gap between two events whose order the bounds do not decide: at least zero (they can share a day), at most
    the larger reach of the two directions."""
    a, b = elapsed(p, q, unit), elapsed(q, p, unit)
    most = None if a.most is None or b.most is None else max(a.most, b.most)
    nominal = abs(a.nominal) if a.nominal is not None else None
    return Elapsed(unit, 0 if most == 0 else nominal if _fine(p, q, unit) else None, 0, most, nominal, p, q)


def _readings(x: Anchor) -> list[Anchor]:
    return list(x.readings) or [x]


def true_at(rows, t) -> list:
    """FACTS rows in force at any moment of t's bounds (FACTS.at, research/33 B.4): two rows when t holds a change."""
    get = lambda r, k: r[k] if isinstance(r, dict) else getattr(r, k)
    lo, hi = t.lo.isoformat(), t.hi.isoformat()
    return sorted((r for r in rows if get(r, "valid_from") < hi and lo < get(r, "valid_to")), key=lambda r: get(r, "valid_from"))


# -------------------------------------------------------------------------------------------- reading the question

_TIMEWORD = (r"(?:week|month|year|weekend|night|time I|summer|winter|spring|autumn|fall|monday|tuesday|wednesday|thursday|friday"
             r"|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december"
             r"|christmas|easter|thanksgiving)")
ORDER_Q = re.compile(r"\b(?:order|earliest|chronological|first to last|most recent(?:ly)?|more recently|latest"
                     rf"|which (?:\w+ ){{0,8}}?(?:first|earlier|later|last(?!\s+{_TIMEWORD}))"
                     r"|(?:who|which|what|happened|did I \w+|came) (?:\w+ ){0,6}?first)\b", re.I)
COUNT_REF = re.compile(r"\bhow many (?!days?\b|weeks?\b|months?\b|years?\b|hours?\b|minutes?\b|times\b)\w+(?: \S+)*? "
                       r"(?:before|after|prior to)\b", re.I)
SINCE_Q = re.compile(r"\bhow (?:many (?:days|weeks|months|years)|long) ago\b|\bhow long (?:has it been|since)\b"
                     r"|\bhow long have I (?:been|had|lived|known|owned|used|worked)\b(?!.*\b(?:when|before|after|by the time)\b)", re.I)
DURATION_Q = re.compile(r"\bhow (?:many|much) (?:days?|weeks?|months?|years?|time)\b|\bhow long\b|\bhow old\b"
                        r"|\b(?:days?|weeks?|months?|years?) (?:passed|between|apart)\b", re.I)
TRUE_AT_Q = re.compile(r"\b(?:who|what|which|where) (?:was|were) (?:my|I)\b|\bwhich \w+(?: \w+)? (?:was|were) (?:I|my)\b"
                       r"|\b(?:was|were) I (?:living|working|using|dating|registered|based|driving|staying|employed)\b"
                       r"|\bwhere did I (?:live|work|stay)\b|\bwho (?:was|were) my\b", re.I)
POINT_Q = re.compile(r"\bwhen did\b|\bwhat (?:date|day)\b|\bwhich day\b|\bon what day\b", re.I)
UNIT_Q = re.compile(r"\bhow many (days?|weeks?|months?|years?)\b|\b(days?|weeks?|months?|years?) ago\b", re.I)
WHOLE_Q = re.compile(r"\border\b|\bearliest to (?:the )?latest\b|\bfirst to last\b|\bchronological", re.I)  # the whole order
FIRST_Q = re.compile(r"\b(?:first|earliest|earlier)\b", re.I)
LAST_Q = re.compile(r"\b(?:most recent(?:ly)?|more recently|latest|last|later)\b", re.I)


def window_of(question: str, as_of: date) -> dates.Timex | None:
    """The time the question names (never a date inside a name: "the March 15th issue"). "since X" runs to now."""
    t = next((t for t in dates.find_timex(question, as_of, "past") if t.type in ("DATE", "TIME") and t.status != "vague"), None)
    if t and t.text.lower().startswith("since") and t.lo:
        return replace(t, hi=as_of + DAY, nominal=None)
    return t


def needs_time(question: str) -> str | None:
    """Which kind of time question this is (36 A.3), by rules over the complexity triggers; None: not about time."""
    cats = categories(question)
    dated = window_of(question, date(2000, 1, 1)) is not None
    if not (cats & {"timespan", "compare", "conditional"} or dated or POINT_Q.search(question)):
        return None
    if SINCE_Q.search(question):  # an amount of time asked for wins over order words ("her latest novel")
        return "since"
    if DURATION_Q.search(question):
        return "duration"
    if ORDER_Q.search(question) or COUNT_REF.search(question):
        return "order"
    if dated and TRUE_AT_Q.search(question):
        return "true_at"
    return "point" if dated or POINT_Q.search(question) else None


def unit_of(question: str) -> str | None:
    if re.search(r"\bhow old\b", question, re.I):
        return "age"
    m = UNIT_Q.search(question)
    return (m[1] or m[2]).lower().rstrip("s") if m else None


# ---------------------------------------------------------------------------------------------- the timeline (B.3)

def when_text(a) -> str:
    """An Anchor's or a Timex's bounds in words."""
    lo, hi, nominal = a.lo, a.hi, a.nominal
    if lo is None and hi is None:
        return "no date"
    if lo is None:
        return f"on or before {fmt(hi - DAY)}"
    if hi is None:
        return f"on or after {fmt(lo)}"
    if (hi - lo).days == 1:
        return fmt(lo)
    if lo.day == 1 and hi == dates.add_months(lo, 1):
        return f"{calendar.month_abbr[lo.month]} {lo.year}"
    if (lo.month, lo.day) == (1, 1) and hi == date(lo.year + 1, 1, 1):
        return str(lo.year)
    span = f"{fmt(lo)} to {fmt(hi - DAY)}"
    return f"{span}, about {fmt(nominal)}" if nominal else span


def _line(i: int, a: Anchor) -> str:
    gran = a.precision
    flag = "; CONFLICT between mentions" if a.conflict else ""
    rs = "; ".join(f"or {when_text(r)}" for r in a.readings[1:])
    head = f"{when_text(a.readings[0]) + ' ' + rs if a.readings else when_text(a)}"
    return f"{i}. {head} | {gran} | {a.how} | {a.words} | {a.why}{flag}\n   [{a.src}]"


def _sortkey(a: Anchor):
    mid = a.nominal or a.lo or a.hi
    return (a.how == "unknown", mid or date.max, a.hi or date.max)


def amount_text(n: int, unit: str) -> str:
    plural = lambda k, w: f"{k} {w}{'' if abs(k) == 1 else 's'}"
    if unit == "week":
        w, d = divmod(n, 7)
        return plural(w, "week") + (f" and {plural(d, 'day')} ({n} days)" if d else f" ({n} days)")
    if unit == "month":
        y, m = divmod(n, 12)
        return " and ".join(x for x in (plural(y, "year") if y else "", plural(m, "month") if m or not y else "") if x) \
            + (f" ({n} months)" if y and m else "")
    return str(n) if unit == "age" else plural(n, unit)


def _elapsed_text(e: Elapsed) -> tuple[str, str]:
    """(answer line, status) for one Elapsed."""
    u, both = e.unit, " (one more if both days are counted)" if e.unit == "day" else ""
    if e.value is not None:
        text = amount_text(e.value, u) + both
        if e.least is not None and e.most is not None and e.least != e.most:
            text += f" (from {amount_text(e.least, u)} to {amount_text(e.most, u)} if the amounts you gave are rounded)"
        elif e.least is not None and e.most is None and e.least != e.value:
            text += f" (at least {amount_text(e.least, u)})"
        elif e.most is not None and e.least is None and e.most != e.value:
            text += f" (at most {amount_text(e.most, u)})"
        return text, "ok"
    if e.least is not None and e.most is not None:
        nom = f"; {amount_text(e.nominal, u)} if the amounts you gave are exact" if e.nominal is not None else ""
        return f"between {amount_text(e.least, u)} and {amount_text(e.most, u)}{nom}", "range"
    one = e.least if e.least is not None else e.most
    nom = f"; {amount_text(e.nominal, u)} if the amounts you gave are exact" if e.nominal is not None else ""
    return (f"{'at least' if e.least is not None else 'at most'} {amount_text(one, u)}{nom}", "range") if one is not None \
        else ("cannot be determined: the dates are open on both sides", "undetermined")


def _caveats(xs) -> list[str]:
    out = []
    for x in xs:
        if x.how == "mentioned" and x.lo is None:
            out.append(f'"{x.words}" has no date of its own: {fmt(x.hi - DAY)} is the day it was mentioned; if it happened'
                       " earlier, the gap to it is longer.")
        if x.conflict:
            out.append(f'"{x.words}": two mentions disagree; {x.how} dating wins.')
        if x.approx:
            out.append(f'"{x.words}" is dated through a vague count (a couple 2-3, a few 2-4, several 3-7); a convention.')
    return out


def _now(as_of: date) -> Anchor:
    return Anchor("now", "the day of the question", "the question", as_of, as_of + DAY, as_of, "day", "explicit",
                  "the question's own date")


def _pick_one(ev: dict, key: str) -> tuple[Anchor | None, list[str]]:
    es = ev[key]
    if len(es) != 1:
        return None, [f'"{key}" matches {len(es)} events: ' + "; ".join(f'"{e.words}" ({when_text(e)})' for e in es)
                      if es else f'"{key}" matches no event on record']
    return es[0], []


def _duration(kind, question, as_of, ev) -> tuple[str, list[str], dict, list[str]]:
    keys = list(ev)
    p, miss = _pick_one(ev, keys[0]) if keys else (None, ["no event given"])
    q, miss2 = (_pick_one(ev, keys[1]) if len(keys) > 1 else (_now(as_of), []))
    miss += miss2
    if miss:
        return "undetermined", ["cannot be determined: " + " ".join(miss)], {"ambiguous": True}, miss
    gone = [x for x in (p, q) if x.how == "unknown"]
    if gone:
        lines = [f'cannot be determined: "{x.words}" has no date on record ({x.why}).' for x in gone]
        plans = {x.eid: x.plan for x in gone if x.plan}
        val = {"missing": [x.eid for x in gone]}
        if len(plans) == len(gone):  # every missing event was planned for a day: say what follows if it went ahead
            pp, qq = plans.get(p.eid, p), plans.get(q.eid, q)
            text, _ = _elapsed_text(elapsed(pp, qq, unit_of(question) or "day"))
            lines.append(f"If it happened on the planned day ({', '.join(when_text(x) for x in plans.values())}): {text}.")
            val["if_planned"] = elapsed(pp, qq, unit_of(question) or "day").value
        return "undetermined", lines, val, [x.words for x in gone]
    rel = relation(p, q)
    if len(keys) > 1 and (rel == "after" or rel == "undetermined" and p.nominal and q.nominal and p.nominal > q.nominal):
        p, q = q, p  # a duration between two events is measured from the earlier
    unit = unit_of(question) or _auto_unit(p, q)
    combos = [(rp, rq) for rp in _readings(p) for rq in _readings(q)]
    # bounded windows that overlap can fall either way round; an open (mention-dated) one is ordered by its nominal,
    # the mention convention of 36 B.4, and gives a one-sided bound instead
    unsure = len(keys) > 1 and relation(p, q) == "undetermined" and all(x.lo and x.hi for x in (p, q))
    got = [(rp, rq, (either_way if unsure else elapsed)(rp, rq, unit)) for rp, rq in combos]
    texts = [_elapsed_text(e) for _, _, e in got]
    val = {"unit": unit, "value": got[0][2].value, "least": min((e.least for *_, e in got if e.least is not None), default=None),
           "most": max((e.most for *_, e in got if e.most is not None), default=None), "nominal": got[0][2].nominal,
           "from": p.eid, "to": q.eid}
    if len({t for t, _ in texts}) == 1:
        text, status = texts[0]
        lines = [f"{text}.", f"From {when_text(p)} to {when_text(q)}."]
    else:
        status = "two_readings"
        val["readings"] = [e.value if e.value is not None else [e.least, e.most] for *_, e in got]
        val["value"] = None
        lines = ["Two readings: " + " ".join(f"if {when_text(rp)} to {when_text(rq)}: {t}." for (rp, rq, _), (t, _) in zip(got, texts))]
    if unsure:
        lines.append("Which of the two came first cannot be told from the dates on record.")
    return status, lines + _caveats((p, q)), val, []


def _auto_unit(p: Anchor, q: Anchor) -> str:
    """"How long" with no unit: days for day-precise inputs, weeks for week ones, else years and months."""
    g = coarser(p.gran or "day", q.gran or "day")
    return {"day": "day", "week": "week"}.get(g, "month")


def _order(question, ev) -> tuple[str, list[str], dict, list[str]]:
    ref = next((k for k in ev if len(ev) > 1 and (i := question.lower().find(k.lower())) > 0
                and re.search(r"\b(?:before|after|prior to)\b(?:\s+\S+){0,2}\s*$", question[:i], re.I)), None)
    every = [e for k, es in ev.items() if k != ref for e in es]
    known = sorted((e for e in every if e.how != "unknown"), key=_sortkey)
    missing = [e for e in every if e.how == "unknown"]
    miss_lines = [f'"{e.words}" has no date on record ({e.why}).' for e in missing]
    if ref:
        r, why = _pick_one(ev, ref)
        if r is None or r.how == "unknown":
            return "undetermined", [f"cannot be determined: {' '.join(why) or r.why}"], {"missing": [ref]}, [ref]
        after = re.search(r"\bafter\s", question, re.I) is not None
        want = "after" if after else "before"
        sure = [e for e in known if relation(e, r) == want]
        maybe = [e for e in known if relation(e, r) in ("undetermined", "same_day")]
        lines = [f"{len(sure)} {want} \"{r.words}\" ({when_text(r)}): " + "; ".join(f'"{e.words}" ({when_text(e)})' for e in sure) + "."]
        if maybe:
            lines.append(f"{len(maybe)} more cannot be placed against it: " + "; ".join(f'"{e.words}" ({when_text(e)})' for e in maybe)
                         + f". The count is {len(sure)} to {len(sure) + len(maybe)}.")
        return ("ok" if not maybe else "range"), lines + miss_lines + _caveats(known), \
            {"count": len(sure), "most": len(sure) + len(maybe), "sure": [e.eid for e in sure], "maybe": [e.eid for e in maybe],
             "ref": r.eid}, [e.words for e in missing]
    ties = [(a, b) for i, a in enumerate(known) for b in known[i + 1:] if relation(a, b) != "before"]
    val = {"order": [e.eid for e in known], "ties": [[a.eid, b.eid] for a, b in ties], "missing": [e.eid for e in missing]}
    lines = ["Oldest first: " + "; ".join(f'{i}. "{e.words}" ({when_text(e)})' for i, e in enumerate(known, 1)) + "."]
    left = list(ties)
    while left:  # name only the undetermined pairs (36 B.6), grouped around the event that ties most
        hub = max(known, key=lambda e: sum(e in t for t in left))
        rivals = [b if a is hub else a for a, b in left if hub in (a, b)]
        left = [t for t in left if hub not in t]
        lines.append(f'"{hub.words}" ({when_text(hub)}) cannot be ordered against ' +
                     "; ".join(f'"{r.words}" ({when_text(r)})' for r in rivals) + ".")
    status = "ok" if not ties and not missing else "undetermined" if not known else "range"
    end = None if WHOLE_Q.search(question) else "first" if FIRST_Q.search(question) else "last" if LAST_Q.search(question) else None
    if end and known:
        pick = known[0] if end == "first" else known[-1]
        rivals = [e for e in known if e is not pick and relation(pick, e) != ("before" if end == "first" else "after")]
        val["pick"] = None if rivals or missing else pick.eid
        lines.insert(0, f'The {"earliest" if end == "first" else "latest"}: "{pick.words}" ({when_text(pick)}).' if val["pick"]
                     else f"Which is {'earliest' if end == 'first' else 'latest'} cannot be determined.")
        status = "ok" if val["pick"] else "undetermined"
    return status, lines + miss_lines + _caveats(known), val, [e.words for e in missing]


def _point(question, as_of, ev) -> tuple[str, list[str], dict, list[str]]:
    every = [e for es in ev.values() for e in es]
    known = [e for e in every if e.how != "unknown"]
    w = window_of(question, as_of)
    if w is None:  # "When did I ...": the dates of the events meant
        lines = [f'"{e.words}": {when_text(e)}.' for e in sorted(known, key=_sortkey)]
        lines += [f'"{e.words}": no date on record ({e.why}).' for e in every if e.how == "unknown"]
        return ("ok" if len(known) == 1 else "undetermined"), lines + _caveats(known), \
            {"dates": {e.eid: [str(e.lo), str(e.hi)] for e in known}}, [e.words for e in every if e.how == "unknown"]
    ws = list(w.readings) or [w]

    def meets(e: Anchor, pad: bool) -> bool:
        """e (any reading) meets the window, widened by its granule when pad (36 B.3)."""
        for r in _readings(e):
            for x in ws:
                p = timedelta(days=max(1, dates.HALF[x.gran]) if pad else 0)
                lo, hi = (x.lo - p if x.lo else None), (x.hi + p if x.hi else None)
                if (r.lo is None or hi is None or r.lo < hi) and (r.hi is None or lo is None or lo < r.hi):
                    return True
        return False

    def off(e: Anchor) -> int:
        """Distance from the window's own day, to rank what meets it; an open-ended (mention-dated) event ranks last."""
        mid = lambda x: x.nominal or (x.lo + (x.hi - x.lo) // 2 if x.lo and x.hi else None)
        got = [abs((mid(r) - mid(x)).days) for r in _readings(e) for x in ws if mid(r) and mid(x) and r.lo]
        return min(got, default=10 ** 6)

    contained = lambda e: any(r.lo and r.hi and x.lo and x.hi and x.lo <= r.lo and r.hi <= x.hi for r in _readings(e) for x in ws)
    inside = sorted((e for e in known if meets(e, False)), key=lambda e: (not contained(e), off(e)))
    near = sorted((e for e in known if e not in inside and meets(e, True)), key=off)
    wtext = f'"{w.text}" ({" or ".join(when_text(x) for x in ws)})'
    val = {"window": [str(w.lo), str(w.hi)], "inside": [e.eid for e in inside], "candidates": [e.eid for e in inside + near]}
    if not inside and not near:
        return "undetermined", [f"cannot be determined: no event on record falls in {wtext}."], val, []
    fit = lambda e: " or ".join(when_text(r) for r in _readings(e) if any(
        (r.lo is None or x.hi is None or r.lo < x.hi) and (r.hi is None or x.lo is None or x.lo < r.hi) for x in ws)) or when_text(e)
    lines = [f"In {wtext}: " + "; ".join(f'"{e.words}" ({fit(e)})' for e in inside) + "." if inside
             else f"Nothing on record falls exactly in {wtext}."]
    if near:
        lines.append("Close to it: " + "; ".join(f'"{e.words}" ({when_text(e)})' for e in near) + ".")
    sure = [e for e in inside if contained(e)]
    status = "ok" if len(inside) == 1 or len(sure) == 1 else "undetermined"
    if status != "ok":
        lines.append("Several events fit; which one the question means is not a date question.")
    return status, lines + _caveats(inside + near), val, []


def _true_at(question, as_of, rows) -> tuple[str, list[str], dict, list[str]]:
    w = window_of(question, as_of) or dates.timex("today", as_of)
    if w.status == "two_readings":
        return "undetermined", [f'cannot be determined: "{w.text}" has two readings.'], {"values": []}, [w.text]
    found = true_at(rows or [], w)
    get = lambda r, k: r[k] if isinstance(r, dict) else getattr(r, k)
    vals = list(dict.fromkeys(get(r, "value") for r in found))
    wtext = f'"{w.text}" ({when_text(w)})'
    if not found:
        return "undetermined", [f"cannot be determined: nothing on record in force during {wtext}."], {"values": []}, [w.text]
    if len(vals) == 1:
        return "ok", [f"In force during {wtext}: {vals[0]}."], {"values": vals}, []
    changes = "; ".join(f"{get(r, 'value')} from {fmt(date.fromisoformat(get(r, 'valid_from')))}" for r in found)
    return "two_readings", [f"It changed during {wtext}: {changes}. Both were true in that period."], {"values": vals}, []


def timeline_for(question: str, as_of: date, anchors: dict, rows=None) -> TimelineResult:
    """The per-question timeline and its computed result. anchors: question phrase -> the events it matched (each an
    Anchor, or a list of Anchors that are mentions of one event, merged here). The order of the phrases is the frame:
    for a duration the first is the from-event and the second the to-event (the reference: "when I made the cake");
    with one phrase it runs to the question's date. For order, a phrase the question puts after "before" or "after"
    is the reference to count against. rows: FACTS rows for "what was true at T"."""
    ev = {k: [merge(e) if isinstance(e, (list, tuple)) else e for e in v] for k, v in anchors.items()}
    timeline = sorted({id(e): e for es in ev.values() for e in es}.values(), key=_sortkey)
    kind = needs_time(question)
    if kind is None and rows is None:
        return TimelineResult(None, question, as_of, "skip", [], timeline)
    if kind == "true_at" or (kind is None and rows is not None):
        kind = "true_at"
        status, lines, val, miss = _true_at(question, as_of, rows)
    elif kind in ("duration", "since"):
        status, lines, val, miss = _duration(kind, question, as_of, ev)
    elif kind == "order":
        status, lines, val, miss = _order(question, ev)
    else:
        status, lines, val, miss = _point(question, as_of, ev)
    return TimelineResult(kind, question, as_of, status, lines, timeline, val, miss)


def render(result: TimelineResult) -> str:
    """The block the reader receives (36 B.5): the computed answer, the timeline with sources, and the instruction to
    phrase it, not compute it. "" when the question is not about time."""
    if result.kind is None:
        return ""
    head = [INSTRUCTION, f"Question: {KIND_LABEL[result.kind]} (asked {fmt(result.as_of)}). Result: {result.answer[0]}",
            *result.answer[1:]]
    tl = ["Timeline, oldest first:", *(_line(i, a) for i, a in enumerate(result.timeline, 1))] if result.timeline else []
    return "<time-computed>\n" + "\n".join(head + tl + [VETO]) + "\n</time-computed>"


def block(question: str, as_of: date, anchors: dict, rows=None) -> str | None:
    """The hook for recall(): the rendered block, or None when the question is not about time or nothing was matched
    to it (a typed fallback: today's path runs unchanged, 36 B.6)."""
    if not rows and not any(e for es in anchors.values() for e in es):
        return None
    r = timeline_for(question, as_of, anchors, rows)
    return render(r) or None


def trace(r: TimelineResult) -> dict:
    """The result as JSON for the result rows (36 C.2: row["time"]), so a miss can be audited without a replay."""
    when = lambda x: x.isoformat() if x else None
    return {"kind": r.kind, "status": r.status, "value": r.value, "answer": r.answer, "missing": r.missing,
            "timeline": [{"eid": a.eid, "words": a.words, "lo": when(a.lo), "hi": when(a.hi), "nominal": when(a.nominal),
                          "precision": a.precision, "how": a.how, "conflict": a.conflict, "src": a.src} for a in r.timeline]}
