"""The time module: grammar, anchoring, the timeline, Allen relations, arithmetic and rendering; no model is called."""
import json
from datetime import date, timedelta

import pytest

from spookling_memory.core import dates as D
from spookling_memory.core import timeline as T

d = date
ONE = timedelta(days=1)
WED, SUN, SAT, TUE = d(2023, 11, 29), d(2022, 4, 10), d(2023, 5, 27), d(2023, 5, 23)

# (text, chat day, tense, type, status, lo, hi, nominal, granule); lo and hi closed-open, the hull for two readings
TIMEX = [
    ("2023-05-01", SAT, None, "DATE", "ok", d(2023, 5, 1), d(2023, 5, 2), d(2023, 5, 1), "day"),
    ("1 May 2023", SAT, None, "DATE", "ok", d(2023, 5, 1), d(2023, 5, 2), d(2023, 5, 1), "day"),
    ("May 1, 2023", SAT, None, "DATE", "ok", d(2023, 5, 1), d(2023, 5, 2), d(2023, 5, 1), "day"),
    ("15th of March 2024", SAT, None, "DATE", "ok", d(2024, 3, 15), d(2024, 3, 16), d(2024, 3, 15), "day"),
    ("May 2023", SAT, None, "DATE", "ok", d(2023, 5, 1), d(2023, 6, 1), None, "month"),
    ("in 2022", SAT, None, "DATE", "ok", d(2022, 1, 1), d(2023, 1, 1), None, "year"),
    ("summer 2023", SAT, None, "DATE", "ok", d(2023, 6, 1), d(2023, 9, 1), None, "season"),
    ("the summer of 2024", SAT, None, "DATE", "ok", d(2024, 6, 1), d(2024, 9, 1), None, "season"),
    ("mid-December 2024", SAT, None, "DATE", "ok", d(2024, 12, 11), d(2024, 12, 21), d(2024, 12, 15), "month"),
    ("early March 2024", SAT, None, "DATE", "ok", d(2024, 3, 1), d(2024, 3, 11), d(2024, 3, 5), "month"),
    ("late February 2024", SAT, None, "DATE", "ok", d(2024, 2, 21), d(2024, 3, 1), d(2024, 2, 25), "month"),
    ("late 2023", SAT, None, "DATE", "ok", d(2023, 9, 1), d(2024, 1, 1), None, "season"),
    ("early 2024", SAT, None, "DATE", "ok", d(2024, 1, 1), d(2024, 5, 1), None, "season"),
    ("Easter 2024", SAT, None, "DATE", "ok", d(2024, 3, 31), d(2024, 4, 1), d(2024, 3, 31), "day"),
    ("Christmas 2023", SAT, None, "DATE", "ok", d(2023, 12, 25), d(2023, 12, 26), d(2023, 12, 25), "day"),
    # a month and day with no year, read by the event's tense: the past "May 1st" is not 2024
    ("on May 1st", WED, "past", "DATE", "ok", d(2023, 5, 1), d(2023, 5, 2), d(2023, 5, 1), "day"),
    ("on May 1st", WED, "future", "DATE", "ok", d(2024, 5, 1), d(2024, 5, 2), d(2024, 5, 1), "day"),
    ("on May 1st", WED, None, "DATE", "ok", d(2024, 5, 1), d(2024, 5, 2), d(2024, 5, 1), "day"),
    ("on October 15th", WED, "past", "DATE", "ok", d(2023, 10, 15), d(2023, 10, 16), d(2023, 10, 15), "day"),
    ("on December 28th", d(2024, 1, 2), "past", "DATE", "ok", d(2023, 12, 28), d(2023, 12, 29), d(2023, 12, 28), "day"),
    ("on February 29th", d(2024, 3, 20), "past", "DATE", "ok", d(2024, 2, 29), d(2024, 3, 1), d(2024, 2, 29), "day"),
    ("on February 29th", d(2023, 3, 20), "past", "DATE", "ok", d(2020, 2, 29), d(2020, 3, 1), d(2020, 2, 29), "day"),
    ("on 5 June", d(2023, 12, 1), "past", "DATE", "ok", d(2023, 6, 5), d(2023, 6, 6), d(2023, 6, 5), "day"),
    ("in June", WED, "past", "DATE", "ok", d(2023, 6, 1), d(2023, 7, 1), None, "month"),
    ("in November", WED, "past", "DATE", "ok", d(2023, 11, 1), d(2023, 12, 1), None, "month"),
    ("last June", WED, None, "DATE", "ok", d(2023, 6, 1), d(2023, 7, 1), None, "month"),
    ("next March", WED, None, "DATE", "ok", d(2024, 3, 1), d(2024, 4, 1), None, "month"),
    ("on the 15th", WED, None, "DATE", "two_readings", d(2023, 11, 15), d(2023, 12, 16), None, "day"),
    ("on the 15th", WED, "past", "DATE", "ok", d(2023, 11, 15), d(2023, 11, 16), d(2023, 11, 15), "day"),
    ("in early May", d(2023, 8, 1), "past", "DATE", "ok", d(2023, 5, 1), d(2023, 5, 11), d(2023, 5, 5), "month"),
    ("last Tuesday", TUE, "past", "DATE", "ok", d(2023, 5, 16), d(2023, 5, 17), d(2023, 5, 16), "day"),
    ("last Friday", SUN, "past", "DATE", "two_readings", d(2022, 4, 1), d(2022, 4, 9), None, "day"),
    ("last Thursday", SAT, "past", "DATE", "two_readings", d(2023, 5, 18), d(2023, 5, 26), None, "day"),
    ("last Monday", d(2023, 5, 28), "past", "DATE", "ok", d(2023, 5, 22), d(2023, 5, 23), d(2023, 5, 22), "day"),
    ("next Tuesday", SUN, None, "DATE", "ok", d(2022, 4, 12), d(2022, 4, 13), d(2022, 4, 12), "day"),
    ("next Friday", TUE, None, "DATE", "two_readings", d(2023, 5, 26), d(2023, 6, 3), None, "day"),
    ("on Friday", SUN, "past", "DATE", "ok", d(2022, 4, 8), d(2022, 4, 9), d(2022, 4, 8), "day"),
    ("on Friday", SUN, None, "DATE", "two_readings", d(2022, 4, 8), d(2022, 4, 16), None, "day"),
    ("this Saturday", SUN, None, "DATE", "ok", d(2022, 4, 9), d(2022, 4, 10), d(2022, 4, 9), "day"),
    # "last weekend" said on a Sunday has two readings
    ("last weekend", SUN, "past", "DATE", "two_readings", d(2022, 4, 2), d(2022, 4, 11), None, "week"),
    ("last weekend", d(2022, 4, 13), "past", "DATE", "ok", d(2022, 4, 9), d(2022, 4, 11), None, "week"),
    ("this weekend", SUN, "past", "DATE", "ok", d(2022, 4, 9), d(2022, 4, 11), None, "week"),
    ("this weekend", d(2022, 4, 13), "future", "DATE", "ok", d(2022, 4, 16), d(2022, 4, 18), None, "week"),
    ("this weekend", d(2022, 4, 11), "past", "DATE", "ok", d(2022, 4, 9), d(2022, 4, 11), None, "week"),
    ("this weekend", SUN, None, "DATE", "two_readings", d(2022, 4, 9), d(2022, 4, 18), None, "week"),
    ("today", SAT, None, "DATE", "ok", SAT, SAT + ONE, SAT, "day"),
    ("yesterday", SAT, None, "DATE", "ok", SAT - ONE, SAT, SAT - ONE, "day"),
    ("the day before yesterday", SAT, None, "DATE", "ok", SAT - 2 * ONE, SAT - ONE, SAT - 2 * ONE, "day"),
    ("tomorrow", SAT, None, "DATE", "ok", SAT + ONE, SAT + 2 * ONE, SAT + ONE, "day"),
    ("tonight", SAT, None, "TIME", "ok", SAT, SAT + ONE, SAT, "day"),
    ("last night", SAT, None, "TIME", "ok", SAT - ONE, SAT, SAT - ONE, "day"),
    ("this morning", SAT, None, "TIME", "ok", SAT, SAT + ONE, SAT, "day"),
    ("two weeks ago", SAT, "past", "DATE", "ok", d(2023, 5, 10), d(2023, 5, 17), d(2023, 5, 13), "week"),
    ("a fortnight ago", SAT, "past", "DATE", "ok", d(2023, 5, 10), d(2023, 5, 17), d(2023, 5, 13), "week"),
    ("10 days ago", SAT, "past", "DATE", "ok", d(2023, 5, 17), d(2023, 5, 18), d(2023, 5, 17), "day"),
    ("exactly two months ago", SAT, "past", "DATE", "ok", d(2023, 3, 27), d(2023, 3, 28), d(2023, 3, 27), "day"),
    ("two months ago", SAT, "past", "DATE", "ok", d(2023, 3, 12), d(2023, 4, 12), d(2023, 3, 27), "month"),
    ("three years ago", SAT, "past", "DATE", "ok", d(2019, 11, 27), d(2020, 11, 26), d(2020, 5, 27), "year"),
    ("a month ago", d(2023, 3, 31), None, "DATE", "ok", d(2023, 2, 13), d(2023, 3, 16), d(2023, 2, 28), "month"),
    ("a month ago", d(2024, 3, 31), None, "DATE", "ok", d(2024, 2, 14), d(2024, 3, 16), d(2024, 2, 29), "month"),
    ("a couple of days ago", SAT, "past", "DATE", "ok", d(2023, 5, 24), d(2023, 5, 26), d(2023, 5, 25), "day"),
    ("a few weeks ago", SAT, "past", "DATE", "ok", d(2023, 4, 29), d(2023, 5, 14), d(2023, 5, 6), "week"),
    ("several days ago", SAT, "past", "DATE", "ok", d(2023, 5, 20), d(2023, 5, 25), None, "day"),
    ("in two weeks", SAT, "future", "DATE", "ok", d(2023, 6, 7), d(2023, 6, 14), d(2023, 6, 10), "week"),
    ("last week", SAT, None, "DATE", "ok", d(2023, 5, 15), d(2023, 5, 22), None, "week"),
    ("last month", d(2024, 1, 10), None, "DATE", "ok", d(2023, 12, 1), d(2024, 1, 1), None, "month"),
    ("this time last year", SAT, None, "DATE", "ok", d(2022, 5, 12), d(2022, 6, 12), d(2022, 5, 27), "month"),
    ("earlier this month", SAT, None, "DATE", "ok", d(2023, 5, 1), d(2023, 5, 27), None, "month"),
    ("in the past two weeks", SAT, None, "DATE", "ok", d(2023, 5, 13), d(2023, 5, 28), None, "week"),
    ("last summer", SAT, None, "DATE", "ok", d(2022, 6, 1), d(2022, 9, 1), None, "season"),
    ("in the summer", SAT, "past", "DATE", "ok", d(2022, 6, 1), d(2022, 9, 1), None, "season"),
    ("last Christmas", SUN, None, "DATE", "ok", d(2021, 12, 25), d(2021, 12, 26), d(2021, 12, 25), "day"),
    ("at Easter", d(2024, 4, 20), "past", "DATE", "ok", d(2024, 3, 31), d(2024, 4, 1), d(2024, 3, 31), "day"),
    ("on Thanksgiving", d(2023, 12, 1), "past", "DATE", "ok", d(2023, 11, 23), d(2023, 11, 24), d(2023, 11, 23), "day"),
    ("Mother's Day", d(2023, 6, 1), "past", "DATE", "ok", d(2023, 5, 14), d(2023, 5, 15), d(2023, 5, 14), "day"),
    ("Ash Wednesday 2024", SAT, None, "DATE", "ok", d(2024, 2, 14), d(2024, 2, 15), d(2024, 2, 14), "day"),
    ("Halloween 2022", SAT, None, "DATE", "ok", d(2022, 10, 31), d(2022, 11, 1), d(2022, 10, 31), "day"),
]


@pytest.mark.parametrize("text,now,tense,typ,status,lo,hi,nominal,gran", TIMEX, ids=[f"{x[0]}@{x[1]}/{x[2]}" for x in TIMEX])
def test_timex_dates(text, now, tense, typ, status, lo, hi, nominal, gran):
    t = D.timex(f"I did it {text}.", now, tense)
    assert t is not None, text
    assert (t.type, t.status, t.lo, t.hi, t.nominal, t.gran) == (typ, status, lo, hi, nominal, gran), t
    if status == "two_readings":
        assert len(t.readings) == 2 and all(r.status == "ok" for r in t.readings)


# (text, amount as said, shortest, longest, to_now, mod) in (months, days)
DURATIONS = [
    ("for six weeks now", (0, 42), (0, 39), (0, 45), True, None),
    ("for the past five years", (60, 0), (60, -182), (60, 182), True, None),
    ("for about 4 years and 3 months now", (51, 0), (51, -15), (51, 15), True, "APPROX"),
    ("for a few months now", (3, 0), (2, 0), (4, 0), True, "APPROX"),
    ("for 9 years", (108, 0), (108, -182), (108, 182), False, None),
    ("over a year", (12, 0), (12, 0), None, False, "MORE_THAN"),
    ("almost a month", (1, 0), (1, -15), (1, 0), False, "LESS_THAN"),
    ("a 5-day trip", (0, 5), (0, 5), (0, 5), False, "EXACT"),
    ("three months in advance", (3, 0), (3, -15), (3, 15), False, None),
    ("a year and a half", (18, 0), (18, -182), (18, 182), False, None),
    ("I'm 32-year-old", (384, 0), (384, 0), (396, -1), True, None),
    ("I'm 32", (384, 0), (384, 0), (396, -1), True, None),
]


@pytest.mark.parametrize("text,amount,shortest,longest,to_now,mod", DURATIONS, ids=[x[0] for x in DURATIONS])
def test_timex_durations(text, amount, shortest, longest, to_now, mod):
    t = D.timex(text, SAT)
    assert t.type == "DURATION" and t.status == "ok"
    assert (t.amount, t.shortest, t.longest, t.to_now, t.mod) == (amount, shortest, longest, to_now, mod), t


SETS = ["every Monday", "on Friday evenings", "on weekends", "twice a week", "every summer", "on Mondays", "weekly",
        "most mornings", "each Saturday"]
NAMES = ["the March 15th issue", "the 2022 model", "the June issue", "the 2019 edition", "the summer collection"]
VAGUE = ["recently", "lately", "the other day", "a while ago", "some time ago", "a long time ago", "ages ago", "back then",
         "in the past", "at some point", "soon", "one of these days", "not long ago", "years ago"]
REFUSED = ["at Diwali", "for Eid", "at Lunar New Year", "during Holi", "at Hanukkah", "when I was a kid", "in my twenties",
           "growing up", "when I was in college"]
NOT_TIME = ["May I ask a thing", "I'll take five", "the 3rd floor", "March on, everyone", "a two-person tent", "Station 12"]


@pytest.mark.parametrize("text", SETS)
def test_sets_are_never_dates(text):
    t = D.timex(f"I go {text}.", SAT, "present")
    assert t.type == "SET" and t.lo is None and t.hi is None


@pytest.mark.parametrize("text", NAMES)
def test_dates_inside_names_are_names(text):
    t = D.timex(f"I read {text} today.", SAT, "past")
    assert t.type == "NAME" and t.lo is None, t


@pytest.mark.parametrize("text", VAGUE + REFUSED)
def test_refuse_list_gives_no_confident_date(text):
    t = D.timex(f"I did it {text}.", SAT, "past")
    assert t is not None and t.status in ("vague", "refused") and t.lo is None and t.hi is None, t


@pytest.mark.parametrize("text", NOT_TIME)
def test_not_time_words(text):
    assert D.timex(text, SAT, "past") is None


def test_two_phases_keep_the_underspecified_value():
    t = D.timex("on May 1st", WED, "past")
    assert (t.rule, t.under, t.value) == ("month_day", "XXXX-05-01", "2023-05-01")
    assert D.timex("last week", SAT).under == "UNDEF-last-week"
    assert D.timex("on Tuesday", SAT, "past").under == "XXXX-WXX-2"
    assert D.timex("for six weeks now", SAT).value == "P6W" and D.timex("I'm 32", SAT).value == "P32Y"


def test_old_grammar_untouched():
    """parse() and find_all() keep their behaviour: the turn brackets, sleep and the events builder use them."""
    assert D.parse("I did it last Tuesday.", TUE, "nearest").vf == d(2023, 5, 16)
    assert D.find_all("I did it recently.", TUE, "nearest") == []


def item(time=None, tense="past", status="happened", **kw):
    return T.Item(kw.pop("id", "x"), kw.pop("event", "did the thing"), "s1#0", time, tense, status, **kw)


@pytest.mark.parametrize("x,now,how,lo,hi,nominal", [
    (item("on October 15th"), WED, "explicit", d(2023, 10, 15), d(2023, 10, 16), d(2023, 10, 15)),
    (item("two weeks ago"), SAT, "relative", d(2023, 5, 10), d(2023, 5, 17), d(2023, 5, 13)),
    (item("for six weeks now", "present", "ongoing"), SAT, "for_now", d(2023, 4, 12), d(2023, 4, 19), d(2023, 4, 15)),
    (item(None), SAT, "mentioned", None, SAT + ONE, SAT),
    (item(None, just=True), SAT, "mentioned", SAT, SAT + ONE, SAT),
    (item("recently"), SAT, "mentioned", None, SAT + ONE, SAT),  # vague words: no more than the day it was said
    (item("in November"), WED, "explicit", d(2023, 11, 1), d(2023, 11, 30), None),  # clamped: not after the chat day
    (item("I'm 32", "present", "ongoing"), SAT, "for_now", d(1990, 5, 28), d(1991, 5, 28), d(1991, 5, 27)),
], ids=["explicit", "relative", "for_now", "mentioned", "just", "vague", "clamped", "age"])
def test_anchor_basis(x, now, how, lo, hi, nominal):
    a = T.anchor(x, now, {})
    assert (a.how, a.lo, a.hi, a.nominal) == (how, lo, hi, nominal), a


@pytest.mark.parametrize("x", [
    item("next month", "future", "planned"), item("on Saturday", status="negated"), item(None, status="hypothetical"),
    item("on Friday evenings", "present", "habitual"), item("the March 15th issue"), item("every Monday", "present", "ongoing"),
    item("three weeks ago", speaker="assistant"), item("at Diwali"), item("when I was a kid"), item("a 5-day trip"),
    item("tomorrow"), item("not a time at all"), item("in March", rel="until"),
], ids=["plan", "negated", "hypothetical", "habit", "name", "set", "assistant", "lunar", "life", "duration_no_end",
        "after_said", "unparsed", "until"])
def test_only_occurrences_get_dates(x):
    """Plans, wishes, habits, negations and names date nothing; only the user's turns date."""
    a = T.anchor(x, SAT, {})
    assert a.how == "unknown" and a.lo is None and a.hi is None and a.why, a


def test_plan_keeps_the_day_it_was_planned_for_but_not_as_its_date():
    a = T.anchor(item("tomorrow", "future", "planned"), d(2023, 4, 23), {})
    assert a.how == "unknown" and a.plan is not None and (a.plan.lo, a.plan.hi) == (d(2023, 4, 24), d(2023, 4, 25))


def test_two_readings_kept_and_one_after_the_chat_dropped():
    a = T.anchor(item("last weekend"), SUN, {})
    assert [(r.lo, r.hi) for r in a.readings] == [(d(2022, 4, 9), d(2022, 4, 11)), (d(2022, 4, 2), d(2022, 4, 4))]
    b = T.anchor(item("this weekend", tense=None), SUN, {})  # current or next: the next is after the chat day
    assert not b.readings and (b.lo, b.hi) == (d(2022, 4, 9), d(2022, 4, 11))


def test_offsets_from_another_event():
    wedding = T.anchor(item("today", id="w"), d(2023, 3, 21), {})
    book = T.anchor(item(None, id="b", anchor_id="w", rel="in_advance", magnitude="three months"), d(2023, 5, 21), {"w": wedding})
    assert book.how == "relative" and book.nominal == d(2022, 12, 21) and book.gran == "month"
    days = T.anchor(item(None, id="c", anchor_id="w", rel="before", magnitude="two days"), d(2023, 5, 21), {"w": wedding})
    assert (days.lo, days.hi, days.gran) == (d(2023, 3, 19), d(2023, 3, 20), "day")
    week = T.anchor(item(None, id="e", anchor_id="w", rel="after", magnitude="a week"), d(2023, 5, 21), {"w": wedding})
    assert (week.lo, week.hi, week.nominal, week.gran) == (d(2023, 3, 25), d(2023, 4, 1), d(2023, 3, 28), "week")
    gone = T.anchor(item(None, id="f", anchor_id="nope", rel="after", magnitude="a week"), SAT, {})
    assert gone.how == "unknown"
    bad = T.anchor(item(None, id="g", anchor_id="w", rel="after", magnitude="on Friday"), SAT, {"w": wedding})
    assert bad.how == "unknown"


def test_anchor_all_dependency_order_and_cycles():
    xs = [item(None, id="b", anchor_id="a", rel="after", magnitude="two days", day=SAT), item("10 days ago", id="a", day=SAT),
          item(None, id="p", anchor_id="q", rel="after", magnitude="a day", day=SAT),
          item(None, id="q", anchor_id="p", rel="after", magnitude="a day", day=SAT)]
    done = T.anchor_all(xs)
    assert done["b"].lo == d(2023, 5, 19) and done["p"].how == done["q"].how == "unknown"


def test_check_drops_what_is_not_verbatim_or_off_the_lists():
    turn = "I just got a smoker today and I'm excited."
    assert T.check(item("today", event="got a smoker"), turn) is None
    assert T.check(item("yesterday", event="got a smoker"), turn) == "time not verbatim"
    assert T.check(item("today", event="bought a grill"), turn) == "event not verbatim"
    assert T.check(item("today", event="got a smoker", status="maybe"), turn) == "status 'maybe'"
    assert T.check(item("today", event="got a smoker", rel="around"), turn) == "rel 'around'"


A = lambda lo, hi, how="explicit", nominal=None, gran="day", eid="e", **kw: T.Anchor(eid, f"event {eid}", f"src {eid}", lo, hi,
                                                                                 nominal, gran, how, **kw)


def test_merge_intersects_bounds():
    m = T.merge([A(None, d(2023, 4, 21), "mentioned", d(2023, 4, 20), eid="a"),
                 A(d(2023, 4, 8), d(2023, 4, 15), "relative", d(2023, 4, 11), "week", eid="b")])
    assert (m.how, m.lo, m.hi, m.nominal, m.conflict) == ("relative", d(2023, 4, 8), d(2023, 4, 15), d(2023, 4, 11), False)
    assert "src a" in m.src and "src b" in m.src


def test_merge_conflict_flagged_and_the_strongest_wins():
    """gpt4_f420262c: the same flight 'recently' on 28 Jan and 'today' on 10 Feb."""
    m = T.merge([A(None, d(2023, 1, 29), "mentioned", d(2023, 1, 28), eid="a"),
                 A(d(2023, 2, 10), d(2023, 2, 11), "relative", d(2023, 2, 10), eid="b")])
    assert m.conflict and m.how == "relative" and m.lo == d(2023, 2, 10)


def test_merge_ignores_unknown_mentions_and_resolves_readings():
    unknown = A(None, None, "unknown", eid="u")
    two = A(d(2022, 4, 2), d(2022, 4, 11), "relative", gran="week", eid="w",
            readings=(A(d(2022, 4, 9), d(2022, 4, 11), "relative", gran="week"), A(d(2022, 4, 2), d(2022, 4, 4), "relative", gran="week")))
    m = T.merge([unknown, two, A(d(2022, 4, 10), d(2022, 4, 11), "explicit", eid="x")])
    assert not m.readings and (m.lo, m.hi) == (d(2022, 4, 10), d(2022, 4, 11))
    assert T.merge([unknown]).how == "unknown"


X = lambda a, b: (A(d(2023, 5, a), d(2023, 5, a + 1)), A(d(2023, 5, b - 1), d(2023, 5, b)))  # extent [a, b) in May

ALLEN = [
    (X(1, 3), X(5, 9), "before"), (X(1, 5), X(5, 9), "meets"), (X(1, 6), X(5, 9), "overlaps"), (X(5, 7), X(5, 9), "starts"),
    (X(6, 8), X(5, 9), "during"), (X(6, 9), X(5, 9), "finishes"), (X(5, 9), X(5, 9), "equals"), (X(5, 9), X(5, 7), "started_by"),
    (X(4, 10), X(5, 9), "contains"), (X(4, 9), X(5, 9), "finished_by"), (X(6, 12), X(5, 9), "overlapped_by"),
    (X(9, 12), X(5, 9), "met_by"), (X(11, 14), X(5, 9), "after"),
    (A(d(2023, 5, 1), d(2023, 6, 1)), A(d(2023, 5, 20), d(2023, 5, 21)), "undetermined"),
    (A(None, d(2023, 5, 6)), A(d(2023, 5, 20), d(2023, 5, 21)), "before"),
]


@pytest.mark.parametrize("p,q,want", ALLEN, ids=[x[2] + str(i) for i, x in enumerate(ALLEN)])
def test_allen(p, q, want):
    assert T.allen(p, q) == want


def test_relation_for_ordering():
    day = lambda n: A(d(2023, 5, n), d(2023, 5, n + 1))
    assert T.relation(day(1), day(2)) == "before" and T.relation(day(3), day(2)) == "after"
    assert T.relation(day(2), day(2)) == "same_day"
    assert T.relation(A(None, d(2023, 4, 9), "mentioned"), day(1)) == "before"
    assert T.relation(A(None, d(2023, 4, 9), "mentioned"), A(d(2023, 3, 25), d(2023, 3, 26))) == "undetermined"
    assert T.possible(A(d(2023, 5, 1), d(2023, 6, 1)), day(20)) == {"before", "meets", "equals", "met_by", "after"}


def P(lo, hi, gran="day", nominal=None, how="explicit", approx=False):
    return A(lo, hi, how, nominal if nominal or not (lo and hi and (hi - lo).days == 1) else lo, gran, approx=approx)


@pytest.mark.parametrize("p,q,unit,value,least,most", [
    (P(d(2023, 1, 5), d(2023, 1, 6)), P(d(2023, 2, 28), d(2023, 3, 1)), "day", 54, 54, 54),
    (P(None, d(2023, 1, 6), nominal=d(2023, 1, 5), how="mentioned"), P(d(2023, 2, 28), d(2023, 3, 1)), "day", 54, 54, None),
    (P(d(2023, 4, 12), d(2023, 4, 19), "week", d(2023, 4, 15)), P(d(2023, 5, 10), d(2023, 5, 17), "week", d(2023, 5, 13)), "week", 28, 22, 34),
    (P(d(2023, 4, 12), d(2023, 4, 19), "week", d(2023, 4, 15)), P(d(2023, 5, 10), d(2023, 5, 17), "week", d(2023, 5, 13)), "day", None, 22, 34),
    (P(d(2023, 6, 1), d(2023, 7, 1), "month"), P(d(2023, 10, 15), d(2023, 10, 16)), "day", None, 107, 136),
    (P(d(2023, 6, 1), d(2023, 7, 1), "month"), P(d(2023, 10, 15), d(2023, 10, 16)), "month", 4, 4, 4),
    (P(d(2023, 1, 31), d(2023, 2, 1)), P(d(2023, 3, 1), d(2023, 3, 2)), "month", 2, 2, 2),
    (P(d(2023, 1, 31), d(2023, 2, 1)), P(d(2023, 3, 1), d(2023, 3, 2)), "week", 29, 29, 29),
    (P(d(2014, 5, 25), d(2014, 5, 26)), P(d(2019, 2, 25), d(2019, 2, 26)), "month", 57, 57, 57),
    (P(d(1990, 5, 28), d(1991, 5, 28), "year", d(1991, 5, 27)), P(d(2018, 5, 27), d(2018, 5, 28)), "age", 27, 27, 27),
    (P(d(1990, 5, 28), d(1991, 5, 28), "year", d(1991, 5, 27)), P(d(2017, 11, 26), d(2018, 11, 27), "year", d(2018, 5, 27)), "age", 27, 26, 28),
    (P(d(2023, 5, 24), d(2023, 5, 26), nominal=d(2023, 5, 25), approx=True), P(SAT, SAT + ONE), "day", None, 2, 3),
    (P(d(2019, 1, 1), d(2020, 1, 1), "year"), P(d(2023, 3, 1), d(2023, 3, 2)), "year", 4, 4, 4),
], ids=["days", "mention_at_least", "weeks_one_number", "days_from_weeks_range", "june_days_range", "june_months",
        "months_field", "weeks_as_days", "4y9m", "age", "age_rounded_move", "vague_count_range", "years_field"])
def test_elapsed_precision(p, q, unit, value, least, most):
    e = T.elapsed(p, q, unit)
    assert (e.value, e.least, e.most) == (value, least, most), e


def test_never_months_through_days():
    """Snodgrass p39: subtract at the field asked; 31 Jan to 28 Feb is one month, 1 Feb to 31 Mar two."""
    assert T.count(d(2023, 1, 31), d(2023, 2, 28), "month") == 1 and T.count(d(2023, 2, 1), d(2023, 3, 31), "month") == 1
    assert T.count(d(2023, 1, 31), d(2023, 3, 1), "month") == 2
    assert T.count(d(1991, 5, 28), d(2018, 5, 27), "age") == 26 and T.count(d(1991, 5, 27), d(2018, 5, 27), "age") == 27


def test_since_is_a_range_to_now():
    w = T.window_of("What did I buy since March?", d(2023, 5, 27))
    assert (w.lo, w.hi) == (d(2023, 3, 1), d(2023, 5, 28))
    assert T.window_of("How many days ago did I read the March 15th issue of The New Yorker?", d(2023, 4, 1)) is None


KINDS = [
    ("How many days passed between the day I cancelled my FarmFresh subscription and the day I did my online grocery shopping?", "duration"),
    ("How many weeks ago did I attend the 'Summer Nights' festival?", "since"),
    ("How many days ago did I attend a baking class when I made my friend's birthday cake?", "since"),
    ("How long have I been taking guitar lessons?", "since"),
    ("How long had I been taking guitar lessons when I bought the new guitar amp?", "duration"),
    ("How long have I been working before I started my current job at NovaTech?", "duration"),
    ("How old was I when I moved to the United States?", "duration"),
    ("How many days did I spend on my solo camping trip to Yosemite?", "duration"),
    ("How many days had passed since I finished reading it when I attended the event about her latest thriller novel?", "duration"),
    ("What is the order of the six museums I visited from earliest to latest?", "order"),
    ("Which streaming service did I start using most recently?", "order"),
    ("How many charity events did I participate in before the 'Run for the Cure' event?", "order"),
    ("Who became a parent first, Rachel or Alex?", "order"),
    ("Which museum did I visit first?", "order"),
    ("Which book did I finish a week ago?", "point"),
    ("What kitchen appliance did I buy 10 days ago?", "point"),
    ("Which restaurant did I go to last Friday?", "point"),
    ("When did I start my current job?", "point"),
    ("Which gym was I a member of in March 2024?", "true_at"),
    ("Who was my GP during April 2026?", "true_at"),
    ("What car was I driving in the summer of 2024?", "true_at"),
    ("What is my dog's name?", None),
    ("What time do I wake up on Tuesdays and Thursdays?", None),
    ("Can you recommend a good book for me?", None),
]


@pytest.mark.parametrize("q,kind", KINDS, ids=[k[0][:40] for k in KINDS])
def test_needs_time(q, kind):
    assert T.needs_time(q) == kind


def test_unit_of():
    assert [T.unit_of(q) for q in ("How many weeks passed between", "How many months ago did I", "How old was I when",
                                   "How long had I been")] == ["week", "month", "age", None]


def tl(q, as_of, anchors, rows=None):
    return T.timeline_for(q, as_of, anchors, rows)


def test_between_two_events_and_render():
    r = tl("How many days passed between the day I cancelled FarmFresh and the day I ordered from Instacart?", d(2023, 3, 18),
           {"cancelled FarmFresh": [A(None, d(2023, 1, 6), "mentioned", d(2023, 1, 5), eid="f")],
            "ordered from Instacart": [A(d(2023, 2, 28), d(2023, 3, 1), "relative", d(2023, 2, 28), eid="i")]})
    assert (r.kind, r.status, r.value["value"], r.value["least"], r.value["most"]) == ("duration", "ok", 54, 54, None)
    text = T.render(r)
    assert text.startswith("<time-computed>\n" + T.INSTRUCTION) and text.endswith(T.VETO + "\n</time-computed>")
    assert "do not compute it again" in text and "Result: 54 days" in text
    assert "on or before 5 Jan 2023 | on_or_before | mentioned | event f" in text and "[src f]" in text
    assert "if it happened earlier, the gap to it is longer" in text


def test_since_runs_to_the_question_date_and_a_when_clause_moves_it():
    cls = [A(d(2022, 3, 20), d(2022, 3, 21), "relative", eid="c")]
    cake = [A(d(2022, 4, 10), d(2022, 4, 11), "relative", eid="k")]
    q = "How many days ago did I attend a baking class when I made my friend's birthday cake?"
    assert tl(q, d(2022, 4, 15), {"baking class": cls, "made the cake": cake}).value["value"] == 21
    assert tl(q, d(2022, 4, 15), {"baking class": cls}).value["value"] == 26


def test_two_readings_computed_both_ways():
    r = tl("How many days ago did I bake the cake?", d(2022, 4, 12), {"bake the cake": [T.anchor(item("last weekend"), SUN, {})]})
    assert r.status == "two_readings" and r.value["value"] is None and len(r.value["readings"]) == 2
    same = tl("How many months ago did I bake the cake?", d(2022, 4, 12), {"bake the cake": [T.anchor(item("last weekend"), SUN, {})]})
    assert same.status == "ok" and same.value["value"] == 0  # both readings give the same answer: one number


def test_undetermined_paths():
    q = "How many days passed between the day I got feedback and the day I tested the suspension?"
    fb = [A(None, d(2023, 3, 18), "mentioned", d(2023, 3, 17), eid="f")]
    plan = T.anchor(item("tomorrow", "future", "planned", id="t"), d(2023, 4, 23), {})
    r = tl(q, d(2023, 6, 1), {"got feedback": fb, "tested the suspension": [plan]})
    assert r.status == "undetermined" and r.value["missing"] == ["t"] and r.value["if_planned"] == 38
    assert "cannot be determined" in r.answer[0] and "If it happened on the planned day" in r.answer[1]
    two = tl(q, d(2023, 6, 1), {"got feedback": fb + [A(d(2023, 3, 1), d(2023, 3, 2), eid="g")], "tested the suspension": fb})
    assert two.status == "undetermined" and two.value["ambiguous"] and "matches 2 events" in two.answer[0]
    habit = tl(q, d(2023, 6, 1), {"got feedback": fb, "tested the suspension": [T.anchor(item("every Monday", "present", "habitual"), SAT, {})]})
    assert habit.status == "undetermined" and "planned" not in " ".join(habit.answer)
    none = tl("Which museum did I visit two weeks ago?", d(2023, 5, 27), {"museum": [A(d(2023, 1, 1), d(2023, 1, 2), eid="m")]})
    assert none.status == "undetermined" and none.value["inside"] == []
    both = tl("Which museum did I visit two weeks ago?", d(2023, 5, 27),
              {"museum": [A(d(2023, 5, 12), d(2023, 5, 13), eid="a"), A(d(2023, 5, 14), d(2023, 5, 15), eid="b")]})
    assert both.status == "undetermined" and set(both.value["inside"]) == {"a", "b"}
    assert T.render(both).count("[src") == 2


def test_between_two_events_whose_order_is_uncertain():
    r = tl("How many days passed between the day I did A and the day I did B?", d(2023, 7, 1),
           {"did A": [A(d(2023, 6, 1), d(2023, 7, 1), gran="month", eid="a")], "did B": [A(d(2023, 6, 20), d(2023, 6, 21), eid="b")]})
    assert (r.value["least"], r.value["most"]) == (0, 19) and "cannot be told" in " ".join(r.answer)


def test_order_ties_named_and_the_rest_stands():
    """gpt4_d6585ce8 from today's context: Billie Eilish only 'on or before 8 Apr'."""
    ev = {"concerts": [A(None, d(2023, 4, 9), "mentioned", d(2023, 4, 8), eid="billie"), A(d(2023, 3, 25), d(2023, 3, 26), eid="park"),
                       A(d(2023, 4, 1), d(2023, 4, 2), eid="brooklyn"), A(d(2023, 4, 15), d(2023, 4, 16), eid="queen")]}
    r = tl("What is the order of the concerts I attended, starting from the earliest?", d(2023, 4, 22), ev)
    assert r.status == "range" and {tuple(sorted(t)) for t in r.value["ties"]} == {("billie", "park"), ("billie", "brooklyn")}
    assert r.value["order"][-1] == "queen" and "pick" not in r.value
    first = tl("Which concert did I go to first?", d(2023, 4, 22), ev)
    assert first.status == "undetermined" and first.value["pick"] is None


def test_order_pick_and_count_before():
    ev = {"services": [A(d(2023, 4, 1), d(2023, 5, 1), gran="month", eid="disney"), A(d(2023, 1, 26), d(2023, 3, 27), "for_now", eid="apple"),
                       A(d(2022, 11, 11), d(2022, 12, 12), "for_now", eid="netflix")]}
    assert tl("Which streaming service did I start using most recently?", d(2023, 5, 26), ev).value["pick"] == "disney"
    assert tl("Which streaming service did I start using first?", d(2023, 5, 26), ev).value["pick"] == "netflix"
    r = tl("How many charity events did I do before the gala?", d(2023, 11, 29),
           {"charity events": ev["services"], "the gala": [A(d(2023, 3, 1), d(2023, 3, 2), eid="gala")]})
    assert (r.value["count"], r.value["most"], r.value["maybe"]) == (1, 2, ["apple"])


def test_point_window_and_true_at():
    ev = {"cooking": [T.anchor(item("last weekend", id="cake"), SUN, {}), A(d(2022, 3, 1), d(2022, 3, 2), eid="old")]}
    r = tl("I mentioned cooking something for my friend a couple of days ago. What was it?", d(2022, 4, 12), ev)
    assert r.kind == "point" and r.status == "ok" and r.value["inside"] == ["cake"]  # only one reading fits
    rows = [{"value": "Dr Okafor", "valid_from": "2023-01-30", "valid_to": "2023-11-02", "from_precision": "on_or_before"},
            {"value": "Dr Brennan", "valid_from": "2023-11-02", "valid_to": "9999-12-31", "from_precision": "day"}]
    assert tl("Who was my GP in June 2023?", d(2024, 1, 1), {}, rows).value["values"] == ["Dr Okafor"]
    change = tl("Who was my GP in November 2023?", d(2024, 1, 1), {}, rows)
    assert change.status == "two_readings" and change.value["values"] == ["Dr Okafor", "Dr Brennan"]
    assert tl("Who was my GP in 2021?", d(2024, 1, 1), {}, rows).status == "undetermined"


def test_not_a_time_question_gives_no_block():
    r = tl("What is my dog's name?", SAT, {"dog": [A(SAT, SAT + ONE)]})
    assert r.kind is None and T.render(r) == "" and T.block("What is my dog's name?", SAT, {"dog": [A(SAT, SAT + ONE)]}) is None
    assert T.block("How many days ago did I buy it?", SAT, {"buy it": []}) is None  # nothing matched: today's path


def test_trace_is_json():
    r = tl("How many days ago did I buy it?", SAT, {"buy it": [A(d(2023, 5, 20), d(2023, 5, 21), eid="b")]})
    t = json.loads(json.dumps(T.trace(r)))
    assert t["value"]["value"] == 7 and t["timeline"][0]["lo"] == "2023-05-20" and t["timeline"][0]["precision"] == "day"
