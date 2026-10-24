"""Date words to dates, in code (research/33 D.3: models do no date arithmetic).

parse(text, now) finds the first date expression in text and returns a Span: the period it names
[start, end) and the point inside it. Absolute: "2025-03-15", "15 March 2025", "mid-December 2024",
"June 2026", "summer 2025", "late 2025", "in 2025", "since March". Relative to now: "today", "this
week", "last month", "two months ago", "last winter", "next week", "starting Monday". Used for the
valid_from hint of a turn (now = the turn's date) and for the date a question names (now = the
question's date). Seasons are meteorological, northern hemisphere; "winter 2024" starts in Dec 2024.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

FOREVER = date(9999, 12, 31)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m} | {"sept": 9}
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "couple of": 2,
           "a few": 3, "few": 3, "several": 3}
SEASONS = {"spring": 3, "summer": 6, "autumn": 9, "fall": 9, "winter": 12}  # first month; each lasts 3
PART_DAY = {"early": 5, "mid": 15, "late": 25}  # research/33 B.4
PART_YEAR = {"early": (1, 5), "mid": (5, 9), "late": (9, 13)}  # month ranges [a, b)

# Month names count only capitalised ("May I ask" is not a date); abbreviations need a following year or day.
_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
_NUM = r"(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|(?:a )?couple of|(?:a )?few|several)"
_YEAR = r"((?:19|20)\d\d)"
_PREP = r"(?:in|during|since|from|by|until|throughout|around|of|back in|as of)"
_FULL_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_NO_YEAR = r"(?!\w)(?!(?: of)?,? ?(?:\d|early|mid|late))"  # "October" but not "October 2024" (that one is absolute)


@dataclass(frozen=True)
class Span:
    start: date
    end: date  # exclusive
    point: date  # the instant a lookup uses when the expression names one ("mid-December" -> the 15th)
    precision: str  # day | week | month | season | year
    kind: str  # absolute | relative
    use_point: bool = False  # True: a point in time ("mid-Dec", "two months ago"); False: the whole period
    text: str = ""

    @property
    def vf(self) -> date:
        """As a valid_from: the point when one is named, else the period's start ("this week" -> Monday)."""
        return self.point if self.use_point else self.start


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), add_months(date(y, m, 1), 1)


def season(y: int, name: str) -> tuple[date, date]:
    a = date(y, SEASONS[name], 1)
    return a, add_months(a, 3)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _n(s: str) -> int:
    s = s.lower()
    return int(s) if s.isdigit() else NUMBERS[s]


def _year_for(m: int, d: int, now: date, mode: str) -> int:
    """The year of a month (and day) named without one. past: the latest on or before now (memory
    questions ask about the past); last: strictly before now's month ("last March"); nearest: the
    occurrence closest to now, either side (a turn may name a coming date: "I start on 14 July")."""
    if mode == "last":
        return now.year if m < now.month else now.year - 1
    cands = [y for y in (now.year - 1, now.year, now.year + 1) if d <= calendar.monthrange(y, m)[1]]
    if mode == "past":
        return max(y for y in cands if (y, m, d) <= (now.year, now.month, now.day) or (y, m) < (now.year, now.month))
    return min(cands, key=lambda y: abs((date(y, m, d) - now).days))


def _season_rel(name: str, which: str, now: date) -> tuple[date, date]:
    spans = [season(y, name) for y in range(now.year - 2, now.year + 2)]
    if which == "last":
        return max(s for s in spans if s[1] <= now)
    if which == "next":
        return min(s for s in spans if s[0] > now)
    return max(s for s in spans if s[0] <= now)  # this: the one now is in, else the latest started


def _abs(kind: str, a: date, b: date, precision: str, point: date | None = None) -> tuple:
    """(kind, start, end, point, precision, use_point): every handler returns this shape."""
    return kind, a, b, point or a + (b - a) // 2, precision, point is not None


def _handlers(now: date, mode: str):
    """(regex, flags, handler(match) -> span tuple) in no particular order; the earliest, longest match wins."""
    def day(y, m, d):
        x = date(y, m, d)
        return _abs("absolute", x, x + timedelta(days=1), "day", x)

    def part_month(part, m, y):
        a, b = month(y, MONTHS[m.lower()])
        return _abs("absolute", a, b, "month", a.replace(day=PART_DAY[part.lower()]))

    def ago(n, unit):
        n, unit = _n(n), unit.lower()
        if unit == "day":
            p = now - timedelta(days=n)
            return "relative", p, p + timedelta(days=1), p, "day", True
        if unit == "week":
            p = now - timedelta(weeks=n)
            return "relative", monday(p), monday(p) + timedelta(days=7), p, "week", True
        if unit == "month":
            p = add_months(now, -n)
            return ("relative", *month(p.year, p.month), p, "month", True)
        p = add_months(now, -12 * n)
        return "relative", date(p.year, 1, 1), date(p.year + 1, 1, 1), p, "year", True

    def rel(a, b, precision, point=None):
        return _abs("relative", a, b, precision, point)

    def weekday(word, name):
        wd, word = WEEKDAYS[name.lower()], (word or "").strip().lower()
        if word in ("next", "starting", "from", "this coming", "coming"):
            d = now + timedelta(days=(wd - now.weekday()) % 7 or 7)
        elif word == "this":  # the one in the current Monday-Sunday week
            d = monday(now) + timedelta(days=wd)
        elif word in ("last", "since last"):  # the most recent one before today
            d = now - timedelta(days=(now.weekday() - wd) % 7 or 7)
        else:  # on / was / since / bare: the most recent one, today included
            d = now - timedelta(days=(now.weekday() - wd) % 7)
        return rel(d, d + timedelta(days=1), "day", d)

    def weekend(which):
        sat = now - timedelta(days=(now.weekday() - 5) % 7)  # the most recent Saturday, today included
        if which == "last" and now.weekday() >= 5:
            sat -= timedelta(days=7)
        if which == "this" and now.weekday() < 5:  # said on a weekday: the coming one
            sat += timedelta(days=7)
        if which == "next":
            sat = monday(now) + timedelta(days=12)
        return rel(sat, sat + timedelta(days=2), "week")

    def ahead(n, unit):
        n, unit = _n(n), unit.lower()
        p = {"day": now + timedelta(days=n), "week": now + timedelta(weeks=n)}.get(unit) or add_months(now, n * (12 if unit == "year" else 1))
        return rel(p, p + timedelta(days=1), unit if unit != "day" else "day", p)

    def so_far(unit):
        a = {"week": monday(now), "month": now.replace(day=1), "year": date(now.year, 1, 1)}[unit.lower()]
        return rel(a, now + timedelta(days=1), unit.lower())

    this_month, this_year = month(now.year, now.month), (date(now.year, 1, 1), date(now.year + 1, 1, 1))
    return [
        (rf"\b{_YEAR}-(\d\d)-(\d\d)\b", 0, lambda g: day(int(g[1]), int(g[2]), int(g[3]))),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? {_MON},? {_YEAR}\b", 0, lambda g: day(int(g[3]), MONTHS[g[2].lower()], int(g[1]))),
        (rf"\b{_MON} (\d{{1,2}})(?:st|nd|rd|th)?,? {_YEAR}\b", 0, lambda g: day(int(g[3]), MONTHS[g[1].lower()], int(g[2]))),
        (rf"\b(early|mid|late)[- ]?{_MON}(?: of)?,? {_YEAR}\b", re.I, lambda g: part_month(g[1], g[2], int(g[3]))),
        (rf"\b{_MON}(?: of)?,? {_YEAR}\b", 0, lambda g: _abs("absolute", *month(int(g[2]), MONTHS[g[1].lower()]), "month")),
        (rf"\b(?:(early|mid|late)[- ]?)?(spring|summer|autumn|fall|winter)(?: of)?,? {_YEAR}\b", re.I,
         lambda g: _abs("absolute", *season(int(g[3]), g[2].lower()), "season")),
        (rf"\b(early|mid|late)[- ]{_YEAR}\b", re.I,
         lambda g: _abs("absolute", date(int(g[2]), PART_YEAR[g[1].lower()][0], 1),
                        add_months(date(int(g[2]), PART_YEAR[g[1].lower()][1] - 1, 1), 1), "month")),
        (rf"\b{_PREP} {_YEAR}\b", re.I, lambda g: _abs("absolute", date(int(g[1]), 1, 1), date(int(g[1]) + 1, 1, 1), "year")),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? {_MON}{_NO_YEAR}", 0,
         lambda g: day(_year_for(MONTHS[g[2].lower()], int(g[1]), now, mode), MONTHS[g[2].lower()], int(g[1]))),
        (rf"\b{_FULL_MON} (\d{{1,2}})(?:st|nd|rd|th)?\b{_NO_YEAR}", 0,
         lambda g: day(_year_for(MONTHS[g[1].lower()], int(g[2]), now, mode), MONTHS[g[1].lower()], int(g[2]))),
        (rf"\b(early|mid|late)[- ]?{_FULL_MON}{_NO_YEAR}", 0,
         lambda g: part_month(g[1], g[2], _year_for(MONTHS[g[2].lower()], PART_DAY[g[1].lower()], now, mode))),
        (rf"\b(last|{_PREP}) {_FULL_MON}{_NO_YEAR}", 0,
         lambda g: _abs("absolute", *month(_year_for(MONTHS[g[2].lower()], 1, now, "last" if g[1] == "last" else mode),
                                          MONTHS[g[2].lower()]), "month")),
        (r"\b(today|tonight|this (?:morning|afternoon|evening))\b", re.I, lambda g: rel(now, now + timedelta(days=1), "day", now)),
        (r"\b(?:yesterday|last night)\b", re.I, lambda g: rel(now - timedelta(days=1), now, "day", now - timedelta(days=1))),
        (r"\bthe day before yesterday\b", re.I, lambda g: rel(now - timedelta(days=2), now - timedelta(days=1), "day", now - timedelta(days=2))),
        (r"\btomorrow\b", re.I, lambda g: rel(now + timedelta(days=1), now + timedelta(days=2), "day", now + timedelta(days=1))),
        (r"\bthe day after tomorrow\b", re.I, lambda g: rel(now + timedelta(days=2), now + timedelta(days=3), "day", now + timedelta(days=2))),
        (r"\b(?:the|over the|at the) weekend\b", re.I, lambda g: weekend("the")),
        (r"\b(this|last|next) weekend\b", re.I, lambda g: weekend(g[1].lower())),
        (r"\b(?:a )?fortnight ago\b", re.I, lambda g: rel(now - timedelta(days=14), now - timedelta(days=13), "day", now - timedelta(days=14))),
        (rf"\bin {_NUM} (day|week|month|year)s?\b", re.I, lambda g: ahead(g[1], g[2])),
        (r"\bearlier this (week|month|year)\b", re.I, lambda g: so_far(g[1])),
        (r"\bthis week\b", re.I, lambda g: rel(monday(now), monday(now) + timedelta(days=7), "week")),
        (r"\blast week\b", re.I, lambda g: rel(monday(now) - timedelta(days=7), monday(now), "week")),
        (r"\bnext week\b", re.I, lambda g: rel(monday(now) + timedelta(days=7), monday(now) + timedelta(days=14), "week")),
        (r"\bthis month\b", re.I, lambda g: rel(*this_month, "month")),
        (r"\blast month\b", re.I, lambda g: rel(*month(add_months(now, -1).year, add_months(now, -1).month), "month")),
        (r"\bnext month\b", re.I, lambda g: rel(*month(add_months(now, 1).year, add_months(now, 1).month), "month")),
        (r"\bthis year\b", re.I, lambda g: rel(*this_year, "year")),
        (r"\blast year\b", re.I, lambda g: rel(date(now.year - 1, 1, 1), this_year[0], "year")),
        (r"\bnext year\b", re.I, lambda g: rel(this_year[1], date(now.year + 2, 1, 1), "year")),
        (rf"\b{_NUM} (day|week|month|year)s? ago\b", re.I, lambda g: ago(g[1], g[2])),
        (r"\b(last|this|next) (spring|summer|autumn|fall|winter)\b", re.I,
         lambda g: rel(*_season_rel(g[2].lower(), g[1].lower(), now), "season")),
        (r"\b(next |starting |from |since last |last |was |on |since |this coming |coming |this )?(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
         0, lambda g: weekday(g[1], g[2])),
    ]


def find_all(text: str, now: date, mode: str = "past") -> list[Span]:
    """Every date expression in text, left to right, resolved against now; where two overlap the earlier (then
    longer) wins. A phrase the grammar does not know ("the other day", "recently") is simply not found: it
    stays unresolved, never guessed."""
    found = []
    for pattern, flags, handle in _handlers(now, mode):
        for m in re.finditer(pattern, text, flags):
            try:
                kind, a, b, p, precision, use_point = handle(m)
            except (ValueError, KeyError, OverflowError):  # "31 February", "2 Septs"
                continue
            found.append((m.start(), -(m.end() - m.start()), m.end(), Span(a, b, p, precision, kind, use_point, m.group(0))))
    out, end = [], -1
    for start, _, stop, span in sorted(found, key=lambda x: (x[0], x[1])):
        if start >= end:
            out.append(span)
            end = stop
    return out


def parse(text: str, now: date, mode: str = "past") -> Span | None:
    """The first (then longest) date expression in text, resolved against now. None if there is none.
    mode says which year a month named without one gets: past (questions) or nearest (a turn's hint)."""
    spans = find_all(text, now, mode)
    return spans[0] if spans else None


def resolve_when(when: str | None, turn_day: date) -> tuple[date, str]:
    """A turn's time words as (valid_from, precision). No words, or words that name no date: the turn's
    own day, precision on_or_before (true by then, start unknown; research/33 B.3.6)."""
    span = parse(when, turn_day, "nearest") if when else None
    return (span.vf, span.precision) if span else (turn_day, "on_or_before")


def day_of(ts: str) -> date:
    return date.fromisoformat(ts[:10])


def fmt(d: date) -> str:
    return "now" if d >= FOREVER else f"{d.day} {calendar.month_abbr[d.month]} {d.year}"


# =====================================================================================================================
# timex(): the time grammar of research/36 B.1, beside parse() (which sleep, the turn brackets and the events builder
# still use, unchanged). HeidelTime's shape [HeidelTime p323-324]: each rule is a pattern, an underspecified value
# (phase 1: "XXXX-05-01", "UNDEF-last-week") and a resolver (phase 2) that fixes it against the chat's date and the
# TENSE of the event the words belong to (the extractor's closed label; there is no tagger here, by design). Output is
# TIMEX3-like [TimeML p.5-6]: type, value, closed-open day bounds [lo, hi) [Snodgrass p91], the day named if the
# amounts said are exact (nominal), a granule [Snodgrass p75], a mod and a status. It never guesses: vague words, two
# readings and calendars we do not ship come back with that status (the refuse list), never as a confident date.

ONE = timedelta(days=1)
GRANS = ("day", "week", "month", "season", "year")  # finest first
HALF = {"day": 0, "week": 3, "month": 15, "season": 45, "year": 182}  # an amount said in a unit is taken as rounded to
# it: "two weeks ago" may lie 3 days either side of chat-14 (a convention, stated in the answer)
VAGUE = {"couple": (2, 3, 2), "few": (2, 4, 3), "several": (3, 7, None)}  # (least, most, nominal): conventions, not facts
VAGUE_NOTE = "a vague count read by convention (a couple 2-3, a few 2-4, several 3-7)"
UNITS = {"day": ((0, 1), "day"), "week": ((0, 7), "week"), "fortnight": ((0, 14), "week"),
         "month": ((1, 0), "month"), "year": ((12, 0), "year")}  # (months, days) per unit, granule
SEASON_CODE = {"spring": "SP", "summer": "SU", "autumn": "FA", "fall": "FA", "winter": "WI"}
PARTS = {"early": "early", "the start of": "early", "the beginning of": "early", "mid": "mid", "the middle of": "mid",
         "late": "late", "the end of": "late"}
PART_MONTH = {"early": (1, 11, 5, "START"), "mid": (11, 21, 15, "MID"), "late": (21, 32, 25, "END")}  # days [a, b), nominal
MODS = {"about": "APPROX", "around": "APPROX", "roughly": "APPROX", "approximately": "APPROX", "over": "MORE_THAN",
        "more than": "MORE_THAN", "just over": "MORE_THAN", "at least": "MORE_THAN", "almost": "LESS_THAN",
        "nearly": "LESS_THAN", "under": "LESS_THAN", "less than": "LESS_THAN", "just under": "LESS_THAN",
        "at most": "LESS_THAN", "exactly": "EXACT"}
EASTER_OFFSET = {"easter": 0, "easter sunday": 0, "easter monday": 1, "good friday": -2, "maundy thursday": -3,
                 "ash wednesday": -46, "palm sunday": -7}
FIXED_HOLIDAYS = {"christmas": (12, 25), "christmas day": (12, 25), "christmas eve": (12, 24), "boxing day": (12, 26),
                  "new years eve": (12, 31), "new years day": (1, 1), "new years": (1, 1), "valentines day": (2, 14),
                  "valentines": (2, 14), "halloween": (10, 31), "independence day": (7, 4), "fourth of july": (7, 4),
                  "the fourth of july": (7, 4), "4th of july": (7, 4), "the 4th of july": (7, 4), "st patricks day": (3, 17)}
NTH_WEEKDAY = {"thanksgiving": (11, 3, 4), "mothers day": (5, 6, 2), "fathers day": (6, 6, 3), "labor day": (9, 0, 1),
               "labour day": (9, 0, 1), "memorial day": (5, 0, -1)}  # (month, weekday, nth; -1 = last), US dates

_CNT = (r"(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|(?:a )?couple(?: of)?|(?:a )?few"
        r"|several)")
_UNIT = r"(day|week|fortnight|month|year)s?"
_AMT = rf"{_CNT} {_UNIT}(?: and a half)?(?:,? and {_CNT} {_UNIT})?"
_MODW = r"(?:(about|around|roughly|approximately|almost|nearly|over|more than|just over|under|less than|just under|exactly|at least|at most) )?"
_WD = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_ORD = r"(?:st|nd|rd|th)?"
_PART = r"(early|mid|late|the start of|the beginning of|the middle of|the end of)"
_HOLI = (r"(Christmas(?: Eve| Day)?|Boxing Day|New Year'?s(?: Eve| Day)?|Valentine'?s(?: Day)?|Halloween|Independence Day"
         r"|(?:the )?(?:Fourth|4th) of July|St\.? Patrick'?s Day|Easter(?: Sunday| Monday)?|Good Friday|Maundy Thursday"
         r"|Ash Wednesday|Palm Sunday|Thanksgiving|Mother'?s'? Day|Father'?s'? Day|Labou?r Day|Memorial Day)")
NAME_NOUNS = r"(?:issue|edition|model|version|release|episode|volume|collection|catalogue|catalog|vintage|special|line-?up|series)"


@dataclass(frozen=True)
class Timex:
    text: str
    rule: str
    type: str  # DATE | TIME | DURATION | SET | NAME
    value: str  # phase 2: 2023-05-01, 2023-05, 2023-W19, 2023-SU, P6W, XXXX-WXX-5
    under: str  # phase 1, before the chat date was applied: XXXX-05-01, UNDEF-last-week
    lo: date | None = None  # closed-open day bounds of where the day can lie; None = open on that side
    hi: date | None = None
    nominal: date | None = None  # the day named if the amounts said are exact
    gran: str = "day"  # day | week | month | season | year
    mod: str | None = None  # APPROX START MID END MORE_THAN LESS_THAN EXACT
    status: str = "ok"  # ok | two_readings | vague | refused
    absolute: bool = False  # explicit words (a date, a month, a holiday) vs relative to the chat's date
    readings: tuple = ()  # two_readings: both, as Timex
    amount: tuple | None = None  # DURATION: (months, days) as said; None for a vague count with no nominal
    shortest: tuple | None = None  # DURATION bounds, (months, days); longest None = open ("over a year")
    longest: tuple | None = None
    to_now: bool = False  # a DURATION that runs up to the chat ("for six weeks now", "for the past 5 years", an age)
    age: bool = False  # "I'm 32": completed years since birth
    note: str = ""  # the convention applied, for the answer to state


def back(d: date, amount: tuple) -> date:
    """d minus an amount of (months, days); months first, clamped to the month's end (31 Jan + 1 month = 28/29 Feb)."""
    return add_months(d, -amount[0]) - timedelta(days=amount[1])


def easter(y: int) -> date:
    """Gregorian Easter Sunday (the anonymous computus)."""
    a, (b, c) = y % 19, divmod(y, 100)
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    mo, dd = divmod(h + ell - 7 * m + 114, 31)
    return date(y, mo, dd + 1)


def holiday(name: str, y: int) -> date:
    key = re.sub(r"\s+", " ", re.sub(r"[’'.]", "", name.lower())).strip()
    if key in FIXED_HOLIDAYS:
        return date(y, *FIXED_HOLIDAYS[key])
    if key in EASTER_OFFSET:
        return easter(y) + timedelta(days=EASTER_OFFSET[key])
    m, wd, nth = NTH_WEEKDAY[key]
    if nth > 0:
        first = date(y, m, 1)
        return first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (nth - 1))
    last = date(y, m, calendar.monthrange(y, m)[1])
    return last - timedelta(days=(last.weekday() - wd) % 7)


def _count(s: str) -> tuple[int, int, int | None]:
    s = s.lower().replace(" of", "").strip()
    s = s[2:] if s.startswith("a ") else s
    return VAGUE[s] if s in VAGUE else (NUMBERS[s],) * 3 if not s.isdigit() else (int(s),) * 3


def _amount(text: str, mod: str | None) -> dict:
    """An amount of time ("six weeks", "about 4 years and 3 months", "a few months", "over a year") as DURATION
    fields: the amount as said, the shortest and longest it can mean, and the finest granule named."""
    nom, least, most, grans, vague = [0, 0], [0, 0], [0, 0], [], False
    for m in re.finditer(rf"{_CNT} {_UNIT}( and a half)?", text, re.I):
        lo_n, hi_n, n = _count(m[1])
        (um, ud), g = UNITS[m[2].lower()]
        half = (um // 2, ud // 2) if m[3] else (0, 0)
        grans.append(g)
        vague |= lo_n != hi_n
        nom = None if nom is None or n is None else [nom[0] + n * um + half[0], nom[1] + n * ud + half[1]]
        least = [least[0] + lo_n * um + half[0], least[1] + lo_n * ud + half[1]]
        most = [most[0] + hi_n * um + half[0], most[1] + hi_n * ud + half[1]]
    gran = min(grans, key=GRANS.index)
    h, amount = HALF[gran], tuple(nom) if nom else None
    if vague:
        lo, hi, note = tuple(least), tuple(most), VAGUE_NOTE
    elif mod == "EXACT":
        lo = hi = amount
        note = ""
    elif mod == "MORE_THAN":
        lo, hi, note = amount, None, ""
    elif mod == "LESS_THAN":
        lo, hi, note = (amount[0], amount[1] - h), amount, ""
    else:
        lo, hi = (amount[0], amount[1] - h), (amount[0], amount[1] + h)
        note = f"taken as rounded to the {gran}" if h else ""
    if lo == hi:
        gran = "day"
    return dict(type="DURATION", value=_pvalue(amount), amount=amount, shortest=lo, longest=hi, gran=gran,
                mod=mod or ("APPROX" if vague else None), note=note)


def _pvalue(amount: tuple | None) -> str:
    if amount is None:
        return "PX"
    m, d = amount
    s = (f"{m // 12}Y" if m >= 12 else "") + (f"{m % 12}M" if m % 12 else "")
    s += (f"{d // 7}W" if d % 7 == 0 else f"{d}D") if d else ""
    return "P" + (s or "0D")


def _span(lo, hi, gran, value, absolute, nominal=None, mod=None, typ="DATE", note="") -> dict:
    return dict(type=typ, value=value, lo=lo, hi=hi, nominal=nominal, gran=gran, absolute=absolute, mod=mod, note=note)


def _on(x: date, absolute: bool = False, typ: str = "DATE", part: str = "") -> dict:
    return _span(x, x + ONE, "day", x.isoformat() + part, absolute, x, typ=typ)


def _week(mon: date) -> dict:
    y, w, _ = mon.isocalendar()
    return _span(mon, mon + timedelta(days=7), "week", f"{y}-W{w:02d}", False)


def _month(y: int, m: int, absolute: bool = True) -> dict:
    return _span(*month(y, m), "month", f"{y}-{m:02d}", absolute)


def _year(y: int, absolute: bool = True) -> dict:
    return _span(date(y, 1, 1), date(y + 1, 1, 1), "year", str(y), absolute)


def _season(y: int, name: str, absolute: bool = True) -> dict:
    return _span(*season(y, name), "season", f"{y}-{SEASON_CODE[name]}", absolute,
                 note="northern-hemisphere seasons assumed")


def _part_month(part: str, y: int, m: int) -> dict:
    a, b, n, mod = PART_MONTH[part]
    end = min(b - 1, calendar.monthrange(y, m)[1])
    return _span(date(y, m, a), date(y, m, end) + ONE, "month", f"{y}-{m:02d}", True, date(y, m, n), mod,
                 note="early/mid/late read as days 1-10, 11-20, 21-end")


def _part_year(part: str, y: int) -> dict:
    a, b = PART_YEAR[part]
    return _span(date(y, a, 1), add_months(date(y, b - 1, 1), 1), "season", str(y), True, None,
                 {"early": "START", "mid": "MID", "late": "END"}[part], note="early/mid/late year read as Jan-Apr, May-Aug, Sep-Dec")


def _by_tense(make, now: date, tense: str | None):
    """A date named without its year, by the tense of its event (HeidelTime phase 2). past: the latest that started on
    or before the chat day; future: the earliest that ends after it; no tense: the one the chat day is in, else both
    readings when both lie within six months, else the nearer."""
    cands = []
    for y in range(now.year - 4, now.year + 5):  # 29 Feb needs four years either side
        try:
            cands.append(make(y))
        except ValueError:  # 29 Feb in a common year
            continue
    past = max((s for s in cands if s["lo"] <= now), key=lambda s: s["lo"], default=None)
    fut = min((s for s in cands if s["hi"] > now), key=lambda s: s["lo"], default=None)
    if past and past["hi"] > now or tense == "past":
        return past
    if tense == "future":
        return fut
    dp, df = (now - past["hi"]).days, (fut["lo"] - now).days
    return [past, fut] if dp < 183 and df < 183 else past if dp <= df else fut


def _weekday(word: str, wd: int, now: date, tense: str | None):
    before = now - timedelta(days=(now.weekday() - wd) % 7 or 7)  # the latest one before today
    after = now + timedelta(days=(wd - now.weekday()) % 7 or 7)  # the first one after today
    if word in ("last", "this past", "past", "since last"):
        timeml = monday(now) - timedelta(days=7 - wd)  # thursday(predecessor(week DCT)) [TimeML p.6]
        return _on(before) if timeml == before or (now - before).days > 3 else [_on(before), _on(timeml)]
    if word == "next":
        timeml = monday(now) + timedelta(days=7 + wd)
        return _on(after) if timeml == after else [_on(after), _on(timeml)]
    if word in ("this coming", "coming", "starting", "from"):
        return _on(after)
    if word == "this":
        return _on(monday(now) + timedelta(days=wd))
    if tense == "past" or word in ("since", "was"):
        return _on(before)
    return _on(after) if tense == "future" else [_on(before), _on(after)]


def _weekend(which: str, now: date, tense: str | None):
    sat = now - timedelta(days=(now.weekday() - 5) % 7)  # the latest Saturday, today included
    span = lambda s: _span(s, s + timedelta(days=2), "week", f"{s.isocalendar()[0]}-W{s.isocalendar()[1]:02d}-WE", False)
    on_weekend = now.weekday() >= 5
    prev, cur, coming = (sat - timedelta(days=7), sat, sat + timedelta(days=7)) if on_weekend else (sat, None, sat + timedelta(days=7))
    if which in ("last", "this past", "past"):
        return [span(cur), span(prev)] if on_weekend else span(prev)
    if which == "next":
        return span(monday(now) + timedelta(days=12))
    if which == "this":
        if on_weekend:
            return span(cur) if tense else [span(cur), span(coming)]
        return span(prev) if tense == "past" else span(coming)
    if on_weekend:  # "the weekend", "over the weekend"
        return span(cur)
    return span(prev) if tense == "past" else span(coming) if tense == "future" else [span(prev), span(coming)]


def _ago(mod: str | None, amt: str, now: date, sign: int) -> dict:
    """N units ago (sign -1) or in N units (sign +1): the chat date shifted by the amount, bounds from its rounding."""
    a = _amount(amt, MODS.get((mod or "").lower()))
    near, far = a["shortest"], a["longest"]
    nominal = None if a["amount"] is None else back(now, a["amount"]) if sign < 0 else now + (now - back(now, a["amount"]))
    if sign < 0:
        lo, hi = (back(now, far) if far else None), back(now, near) + ONE
    else:
        lo, hi = now + (now - back(now, near)), (now + (now - back(now, far)) + ONE) if far else None
    v = nominal or lo or hi
    y, w, _ = v.isocalendar()
    value = {"day": v.isoformat(), "week": f"{y}-W{w:02d}", "month": f"{v.year}-{v.month:02d}"}.get(a["gran"], str(v.year))
    return _span(lo, hi, a["gran"], value, False, nominal, a["mod"], note=a["note"])


def _since_start(unit: str, now: date) -> date:
    return {"week": monday(now), "month": now.replace(day=1), "year": date(now.year, 1, 1)}[unit]


def _earlier_this(unit: str, now: date) -> dict:
    """"earlier this month": from the period's start up to the day before the chat (the chat day itself on its first day)."""
    a = _since_start(unit, now)
    b = max(now, a + ONE)
    return _span(a, b, unit, f"{a.isoformat()}/{(b - ONE).isoformat()}", False)


def _period(which: str, unit: str, now: date) -> dict:
    if unit == "week":
        return _week(monday(now) + timedelta(days=7 * {"last": -1, "this": 0, "next": 1}[which]))
    if unit == "month":
        p = add_months(now, {"last": -1, "this": 0, "next": 1}[which])
        return _month(p.year, p.month, False)
    return _year(now.year + {"last": -1, "this": 0, "next": 1}[which], False)


def _hol(which: str | None, name: str, year: str | None, now: date, tense: str | None):
    if year:
        return _on(holiday(name, int(year)), True)
    days = [holiday(name, y) for y in range(now.year - 2, now.year + 3)]
    if which == "last":
        return _on(max(d for d in days if d < now), True)
    if which == "next":
        return _on(min(d for d in days if d > now), True)
    if which == "this":
        return _on(holiday(name, now.year), True)
    return _by_tense(lambda y: _on(holiday(name, y), True), now, tense)


def _nth(d: int, now: date, tense: str | None):
    """"on the 15th", no month: the latest or the next such day, by tense; both when there is no tense."""
    cands = [date(p.year, p.month, d) for k in range(-3, 4) if d <= calendar.monthrange(
        (p := add_months(now.replace(day=1), k)).year, p.month)[1]]
    past, fut = max(c for c in cands if c <= now), min(c for c in cands if c >= now)
    if past == now or tense == "past":
        return _on(past, True)
    return _on(fut, True) if tense == "future" else [_on(past, True), _on(fut, True)]


def _window(cnt: str | None, unit: str, now: date) -> dict:
    """"in the past two weeks": from that far back up to the chat day."""
    a = _amount(f"{cnt or 'one'} {unit}", "EXACT")
    lo = back(now, a["longest"])
    return _span(lo, now + ONE, UNITS[unit.lower()][1], f"{lo.isoformat()}/{now.isoformat()}", False)


def _season_named(which: str, name: str, now: date) -> dict:
    a, b = _season_rel(name, which, now)
    return _span(a, b, "season", f"{a.year}-{SEASON_CODE[name]}", False, note="northern-hemisphere seasons assumed")


DAY_WORDS = {"today": (0, ""), "earlier today": (0, "T"), "tonight": (0, "TNI"), "this morning": (0, "TMO"),
             "this afternoon": (0, "TAF"), "this evening": (0, "TEV"), "yesterday": (-1, ""), "last night": (-1, "TNI"),
             "the day before yesterday": (-2, ""), "tomorrow": (1, ""), "the day after tomorrow": (2, "")}


def _dayword(word: str, now: date) -> dict:
    shift, part = DAY_WORDS[word.lower()]
    return _on(now + timedelta(days=shift), typ="TIME" if part else "DATE", part=part)


def _refuse(status: str, note: str):
    return lambda m: (f"UNDEF-{status}", lambda now, tense: dict(type="DATE", value="", status=status, note=note))


def _r(under, fn):
    """A phase-1 result: the underspecified value, and the phase-2 resolver fn(now, tense)."""
    return lambda m: (under(m) if callable(under) else under, lambda now, tense: fn(m, now, tense))


_SETV = {"weekend": "XXXX-WXX-WE", "day": "P1D", "morning": "XXXX-XX-XXTMO", "evening": "XXXX-XX-XXTEV",
         "night": "XXXX-XX-XXTNI", "week": "P1W", "month": "P1M", "year": "P1Y"}


def _set_value(text: str) -> str:
    t = text.lower()
    for i, d in enumerate(calendar.day_name):
        if d.lower() in t:
            return f"XXXX-WXX-{i + 1}"
    return next((v for k, v in _SETV.items() if k in t), "SET")


# (rule id, pattern, flags, phase 1). Earliest match wins, then the longest, then the first listed: the refuse list,
# sets and names come first so that "on Friday evenings" is a SET, never a Friday, and "the March 15th issue" a NAME.
TIMEX_RULES = [
    ("refuse_calendar", r"\b(Holi|Diwali|Deepavali|Eid(?: al[- ](?:Fitr|Adha))?|Lunar New Year|Chinese New Year|Tet|Hanukkah"
     r"|Chanukah|Rosh Hashanah|Yom Kippur|Passover|Ramadan|Vesak|Navratri|Chuseok|Songkran|Mid-Autumn Festival|Purim|Sukkot)\b",
     0, _refuse("refused", "a holiday on a lunar or other calendar; no per-year table is shipped")),
    ("refuse_life", r"\b(?:when I was (?:a kid|a child|little|young|younger|a teenager|a teen|in (?:high school|middle school"
     r"|college|university|school)|at (?:uni|university|school|college))|in my (?:teens|twenties|thirties|forties|fifties"
     r"|sixties|[2-6]0s)|as a (?:kid|child|teenager|teen)|growing up|in my childhood|back in (?:high school|college|school))\b",
     re.I, _refuse("refused", "a period of life; needs an age on record")),
    ("vague", r"\b(?:recently|lately|the other day|a (?:little |short )?while (?:ago|back)|some ?time (?:ago|back)|a long time ago"
     r"|long ago|ages ago|back then|in the past|at some point|soon|one day|one of these days|not long ago|a bit ago"
     r"|years ago|months ago|weeks ago|in the old days|way back|once upon a time|previously|before long)\b",
     re.I, _refuse("vague", "vague time words; the only bound is the day it was said")),
    ("set_every", rf"\b(?:every|each) (?:other |single )?(?:day|morning|afternoon|evening|night|week|weekend|month|year"
     rf"|summer|winter|spring|autumn|fall|{_WD})\b", re.I,
     _r(lambda m: _set_value(m[0]), lambda m, now, t: dict(type="SET", value=_set_value(m[0])))),
    ("set_weekdays", rf"\b(?:(?:on|at) )?{_WD}s\b|\b(?:on )?{_WD} (?:mornings|afternoons|evenings|nights)\b"
     r"|\b(?:on|at|over|during) (?:the )?weekends\b|\b(?:most|some) (?:days|mornings|evenings|nights|weekends)\b"
     r"|\bin the (?:mornings|evenings)\b", re.I,
     _r(lambda m: _set_value(m[0]), lambda m, now, t: dict(type="SET", value=_set_value(m[0])))),
    ("set_freq", rf"\b(?:once|twice|thrice|{_CNT} times) (?:a|per|every) (?:day|week|fortnight|month|year)\b"
     r"|\b(?:daily|weekly|fortnightly|monthly|yearly|annually|nightly|everyday)\b", re.I,
     _r(lambda m: _set_value(m[0]), lambda m, now, t: dict(type="SET", value=_set_value(m[0])))),
    ("name", rf"\b(?:{_FULL_MON} \d{{1,2}}{_ORD}|\d{{1,2}}{_ORD} {_FULL_MON}|{_FULL_MON}(?:,? {_YEAR})?|{_YEAR}"
     rf"|(?i:spring|summer|autumn|fall|winter)(?: {_YEAR})?)(?:'s)? (?i:{NAME_NOUNS})\b", 0,
     _r("NAME", lambda m, now, t: dict(type="NAME", value="NAME", note="a date inside a name"))),
    ("iso", rf"\b{_YEAR}-(\d\d)-(\d\d)\b", 0, _r(lambda m: m[0], lambda m, now, t: _on(date(int(m[1]), int(m[2]), int(m[3])), True))),
    ("dmy", rf"\b(\d{{1,2}}){_ORD}(?: of)? {_MON},? {_YEAR}\b", 0,
     _r(lambda m: m[0], lambda m, now, t: _on(date(int(m[3]), MONTHS[m[2].lower()], int(m[1])), True))),
    ("mdy", rf"\b{_MON} (\d{{1,2}}){_ORD},? {_YEAR}\b", 0,
     _r(lambda m: m[0], lambda m, now, t: _on(date(int(m[3]), MONTHS[m[1].lower()], int(m[2])), True))),
    ("part_month_year", rf"\b(?i:{_PART})[- ]?{_MON}(?: of)?,? {_YEAR}\b", 0,
     _r(lambda m: f"{m[3]}-{MONTHS[m[2].lower()]:02d}", lambda m, now, t: _part_month(PARTS[m[1].lower()], int(m[3]), MONTHS[m[2].lower()]))),
    ("month_year", rf"\b{_MON}(?: of)?,? {_YEAR}\b", 0,
     _r(lambda m: f"{m[2]}-{MONTHS[m[1].lower()]:02d}", lambda m, now, t: _month(int(m[2]), MONTHS[m[1].lower()]))),
    ("season_year", rf"\b(?i:(?:(early|mid|late)[- ])?(?:the )?(spring|summer|autumn|fall|winter)(?: of)?),? {_YEAR}\b", 0,
     _r(lambda m: f"{m[3]}-{SEASON_CODE[m[2].lower()]}", lambda m, now, t: _season(int(m[3]), m[2].lower()))),
    ("part_year", rf"\b(?i:(early|mid|late|the start of|the beginning of|the middle of|the end of))[- ]{_YEAR}\b", 0,
     _r(lambda m: m[2], lambda m, now, t: _part_year(PARTS[m[1].lower()], int(m[2])))),
    ("year", rf"\b(?i:{_PREP}) {_YEAR}\b", 0, _r(lambda m: m[1], lambda m, now, t: _year(int(m[1])))),
    ("holiday", rf"\b(?:(?i:(last|this|next)) )?(?i:{_HOLI})(?:,? {_YEAR})?\b", 0,
     _r(lambda m: f"XXXX-{m[2]}", lambda m, now, t: _hol((m[1] or "").lower() or None, m[2], m[3], now, t))),
    ("day_month", rf"\b(\d{{1,2}}){_ORD}(?: of)? {_MON}{_NO_YEAR}", 0,
     _r(lambda m: f"XXXX-{MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}",
        lambda m, now, t: _by_tense(lambda y: _on(date(y, MONTHS[m[2].lower()], int(m[1])), True), now, t))),
    ("month_day", rf"\b{_FULL_MON} (\d{{1,2}}){_ORD}\b{_NO_YEAR}", 0,
     _r(lambda m: f"XXXX-{MONTHS[m[1].lower()]:02d}-{int(m[2]):02d}",
        lambda m, now, t: _by_tense(lambda y: _on(date(y, MONTHS[m[1].lower()], int(m[2])), True), now, t))),
    ("part_month", rf"\b(?i:{_PART})[- ]?{_FULL_MON}{_NO_YEAR}", 0,
     _r(lambda m: f"XXXX-{MONTHS[m[2].lower()]:02d}",
        lambda m, now, t: _by_tense(lambda y: _part_month(PARTS[m[1].lower()], y, MONTHS[m[2].lower()]), now, t))),
    ("month", rf"\b(?:(last|this|next)|(?i:{_PREP})) {_FULL_MON}{_NO_YEAR}", 0,
     _r(lambda m: f"XXXX-{MONTHS[m[2].lower()]:02d}", lambda m, now, t: (
         _month(_year_for(MONTHS[m[2].lower()], 1, now, "last"), MONTHS[m[2].lower()]) if m[1] == "last"
         else _month(now.year, MONTHS[m[2].lower()]) if m[1] == "this"
         else _by_tense(lambda y: _month(y, MONTHS[m[2].lower()]), now, "future") if m[1] == "next"
         else _by_tense(lambda y: _month(y, MONTHS[m[2].lower()]), now, t)))),
    ("the_nth", rf"\bon the (\d{{1,2}}){_ORD}\b(?! of)", re.I,
     _r(lambda m: f"XXXX-XX-{int(m[1]):02d}", lambda m, now, t: _nth(int(m[1]), now, t))),
    ("day_word", r"\b(?:the day (?:before yesterday|after tomorrow)|today|earlier today|tonight|this (?:morning|afternoon|evening)"
     r"|yesterday|last night|tomorrow)\b", re.I,
     _r(lambda m: f"UNDEF-{m[0].lower()}", lambda m, now, t: _dayword(m[0], now))),
    ("weekend", r"\b(last|this past|past|this|next|the|over the|at the) weekend\b", re.I,
     _r(lambda m: f"UNDEF-{m[1].lower()}-weekend", lambda m, now, t: _weekend(m[1].lower(), now, t))),
    ("fortnight_ago", r"\ba fortnight ago\b", re.I, _r("UNDEF-P2W-ago", lambda m, now, t: _ago(None, "two weeks", now, -1))),
    ("ago", rf"\b{_MODW}({_CNT} {_UNIT}(?: and a half)?) (?:ago|back)\b", re.I,
     _r(lambda m: f"UNDEF-{m[2]}-ago", lambda m, now, t: _ago(m[1], m[2], now, -1))),
    ("ahead", rf"\bin {_MODW}({_CNT} {_UNIT}(?: and a half)?)(?: time)?\b|\b{_MODW}({_CNT} {_UNIT}) from now\b", re.I,
     _r(lambda m: f"UNDEF-{m[2] or m[8]}-ahead", lambda m, now, t: _ago(m[1] or m[7], m[2] or m[8], now, 1))),
    ("window", rf"\b(?:(?:in|over|during|within) )?the (?:past|last) (?:{_CNT} )?(day|week|fortnight|month|year)s?\b(?! of)", re.I,
     _r(lambda m: f"UNDEF-past-{m[2]}", lambda m, now, t: _window(m[1], m[2], now))),
    ("earlier_this", r"\bearlier this (week|month|year)\b", re.I,
     _r(lambda m: f"UNDEF-this-{m[1]}", lambda m, now, t: _earlier_this(m[1].lower(), now))),
    ("period", r"\b(this|last|next) (week|month|year)\b", re.I,
     _r(lambda m: f"UNDEF-{m[1].lower()}-{m[2].lower()}", lambda m, now, t: _period(m[1].lower(), m[2].lower(), now))),
    ("season_rel", r"\b(last|this|next) (spring|summer|autumn|fall|winter)\b", re.I,
     _r(lambda m: f"UNDEF-{m[1].lower()}-{m[2].lower()}", lambda m, now, t: _season_named(m[1].lower(), m[2].lower(), now))),
    ("season", r"\b(?:in|during|over) (?:the )?(spring|summer|autumn|fall|winter)\b(?!,? ?(?:of )?\d)", re.I,
     _r(lambda m: f"XXXX-{SEASON_CODE[m[1].lower()]}", lambda m, now, t: _by_tense(
         lambda y: _season(y, m[1].lower()), now, t))),
    ("this_time_last_year", r"\bthis time last year\b", re.I,
     _r("UNDEF-today-P1Y", lambda m, now, t: _span(add_months(now, -12) - timedelta(days=15), add_months(now, -12) + timedelta(days=16),
                                                    "month", f"{now.year - 1}-{now.month:02d}", False, add_months(now, -12)))),
    ("weekday", rf"\b(?:(last|this past|past|this coming|coming|this|next|on|since last|since|from|starting|was) )?({_WD})\b", re.I,
     _r(lambda m: f"XXXX-WXX-{WEEKDAYS[m[2].lower()] + 1}",
        lambda m, now, t: _weekday((m[1] or "").lower(), WEEKDAYS[m[2].lower()], now, t))),
    ("age", r"\bI(?:'m|’m| am) (?:an? )?(\d{1,3})(?:[- ]years?[- ]old)?(?=[\s,.;:!?)]|$)"
     r"(?!\s*(?:minutes|hours|days|weeks|months|miles|km|kilometers|percent|dollars|pounds|euros|kg|lbs|feet|ft|inches"
     r"|times|of\b|out\b|in\b|on\b))", re.I,
     _r(lambda m: f"P{m[1]}Y", lambda m, now, t: dict(type="DURATION", value=f"P{m[1]}Y", amount=(12 * int(m[1]), 0),
                                                       shortest=(12 * int(m[1]), 0), longest=(12 * int(m[1]) + 12, -1),
                                                       gran="year", to_now=True, age=True,
                                                       note="an age counts completed years"))),
    ("for_past", rf"\bfor the (?:past|last) {_MODW}({_AMT})\b", re.I,
     _r(lambda m: "UNDEF-duration", lambda m, now, t: _amount(m[2], MODS.get((m[1] or "").lower())) | {"to_now": True})),
    ("for_now", rf"\bfor {_MODW}({_AMT})(?: (now|so far|already))?\b", re.I,
     _r(lambda m: "UNDEF-duration", lambda m, now, t: _amount(m[2], MODS.get((m[1] or "").lower())) | {"to_now": bool(m[m.re.groups])})),
    ("amount_now", rf"\b{_MODW}({_AMT}) (?:now|so far)\b", re.I,
     _r(lambda m: "UNDEF-duration", lambda m, now, t: _amount(m[2], MODS.get((m[1] or "").lower())) | {"to_now": True})),
    ("extent", r"\ban? (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[- ](day|week|month|year)(?:[- ]long)?\b",
     re.I, _r(lambda m: "UNDEF-duration", lambda m, now, t: _amount(f"{m[1]} {m[2]}", "EXACT"))),
    ("amount", rf"\b{_MODW}({_AMT})\b", re.I,
     _r(lambda m: "UNDEF-duration", lambda m, now, t: _amount(m[2], MODS.get((m[1] or "").lower())))),
]
_TIMEX = [(rid, re.compile(p, f), fn) for rid, p, f, fn in TIMEX_RULES]


def _build(text: str, rule: str, under: str, got) -> Timex:
    if isinstance(got, list):  # two readings: both kept, the bounds are their hull
        rs = tuple(_build(text, rule, under, g) for g in got)
        los, his = [r.lo for r in rs], [r.hi for r in rs]
        return Timex(text, rule, rs[0].type, " | ".join(r.value for r in rs), under, None if None in los else min(los),
                     None if None in his else max(his), None, max((r.gran for r in rs), key=GRANS.index), None,
                     "two_readings", rs[0].absolute, rs, note="two readings kept")
    return Timex(text=text, rule=rule, under=under, **got)


def find_timex(text: str, chat_day: date, tense: str | None = None) -> list[Timex]:
    """Every time expression in text, left to right: phase 1 (the rules), then phase 2 against the chat's date and the
    tense of the event the words belong to (past | present | future | None)."""
    found = []
    for order, (rid, rx, fn) in enumerate(_TIMEX):
        for m in rx.finditer(text):
            found.append((m.start(), -(m.end() - m.start()), order, m.end(), rid, m, fn))
    out, end = [], -1
    for start, _, _, stop, rid, m, fn in sorted(found, key=lambda x: x[:3]):
        if start < end:
            continue
        under, resolve = fn(m)
        try:
            got = resolve(chat_day, tense)
        except (ValueError, KeyError, OverflowError):  # "31 February"
            continue
        if got is None:
            continue
        out.append(_build(m[0], rid, under, got))
        end = stop
    return out


def amount(text: str) -> Timex | None:
    """Text read only as an amount of time: a TLINK magnitude ("three months in advance", "one day after") is a
    duration by definition [TimeML p.9], so "one day" here is 24 hours, never the vague "someday"."""
    m = re.search(rf"\b{_MODW}({_AMT})\b", text, re.I)
    return Timex(text=m[0], rule="amount", under="UNDEF-duration", **_amount(m[2], MODS.get((m[1] or "").lower()))) if m else None


def timex(text: str, chat_day: date, tense: str | None = None) -> Timex | None:
    """The first (then longest) time expression in text as a Timex, or None when the text names no time."""
    found = find_timex(text, chat_day, tense)
    return found[0] if found else None


if __name__ == "__main__":  # smallest self-check; the real tests are tests/test_spookling_adapter.py
    now = date(2027, 3, 4)
    assert parse("Which phone was I using in mid-December 2024?", now).point == date(2024, 12, 15)
    assert parse("in June 2026", now).start == date(2026, 6, 1) and not parse("in June 2026", now).use_point
    assert parse("two months ago", now).start == date(2027, 1, 1)
    assert parse("last winter", now).start == date(2026, 12, 1)
    assert parse("May I ask a thing", now) is None
    assert resolve_when("this week", date(2025, 11, 1)) == (date(2025, 10, 27), "week")
    print("ok")
