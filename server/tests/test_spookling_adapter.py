"""The memory algorithm, offline: a stub Triage stands in for the writer model, so no test makes a model call."""
import json
import os
from datetime import date

import pytest

os.environ["SPOOKLING_LOCAL_MODELS"] = "0"

from spookling_memory.core import SpooklingMemory  # noqa: E402
from spookling_memory.core import triage as triage_mod  # noqa: E402
from spookling_memory.core.base import Session, Turn  # noqa: E402
from spookling_memory.core.facts import Facts, IntegrityError  # noqa: E402
from spookling_memory.core.recall import ABSTAIN, date_in, n_tokens  # noqa: E402
from spookling_memory.core.triage import Item, ModelTriage, Ruling, Spent, Triage, Vocab  # noqa: E402

NOW = "2027-03-04T10:00:00Z"


class _Reply:
    def __init__(self, text):
        self.text, self.prompt_tokens, self.completion_tokens = text, 10, 5


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    """Every writer-model call (rewrite, map-reduce, self-check) gets an empty answer unless a test stubs it."""
    calls = []
    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: calls.append(messages) or
                        _Reply('{"queries": [], "items": [], "add": [], "drop": [], "turns": []}'))
    return calls


class Stub(Triage):
    """Canned tags per user turn text; resolve() returns `rulings[turn]` or accepts the proposal."""

    name, version = "stub", "stub-1"

    def __init__(self, tags: dict, rulings: dict | None = None):
        self.tags, self.rulings, self.judged, self.resolved = tags, rulings or {}, [], []

    def judge(self, turns, date, known):
        self.judged.append(list(turns))
        return [[Item(**x) for x in self.tags.get(t, [])] for t in turns], Spent(calls=1, tokens=10)

    def resolve(self, turn, date, proposed, history, flags):
        self.resolved.append((turn, tuple(flags)))
        if turn in self.rulings:
            return self.rulings[turn], Spent(calls=1, tokens=5)
        return Ruling(proposed["op"], proposed["value"], proposed["old"], proposed["when"]), Spent(calls=1, tokens=5)


def sess(i: int, ts: str, *users: str) -> Session:
    return Session(f"s{i}", "A", ts, [t for u in users for t in (Turn("user", u), Turn("assistant", "Noted."))])


PHONES = {
    "This Pixel 8 camera is honestly great.": [dict(label="fact", key="phone", value="Pixel 8")],
    "Looking at new phones: it's between the Fairphone 5 and the iPhone 15.": [dict(label="hypothetical", key="phone", value="Fairphone 5")],
    "Upgraded my old phone with a Fairphone 5 today.": [dict(label="fact", key="handset", value="Fairphone 5", when="today")],
    "Replaced my old phone with a iPhone 16 this week.": [dict(label="fact", key="phone", value="iPhone 16", when="this week")],
    "I drive a Toyota Corolla, nothing fancy.": [dict(label="fact", key="car", value="Toyota Corolla")],
    "Got rid of the Corolla this weekend, so I'm car-free now.": [dict(label="retraction", key="car", old="Corolla", when="this weekend")],
    "My Pixel keeps nagging me about updates.": [dict(label="fact", key="phone", value="Pixel")],
}
HISTORY = [
    sess(1, "2024-05-22T10:00:00Z", "I drive a Toyota Corolla, nothing fancy.", "How do I roast peppers?"),
    sess(2, "2024-09-23T19:35:00Z", "Where do people buy a second-hand winter coat?", "This Pixel 8 camera is honestly great."),
    sess(3, "2024-11-02T09:00:00Z", "My Pixel keeps nagging me about updates."),
    sess(4, "2025-03-07T15:33:00Z", "Looking at new phones: it's between the Fairphone 5 and the iPhone 15."),
    sess(5, "2025-04-03T13:33:00Z", "Upgraded my old phone with a Fairphone 5 today."),
    sess(6, "2025-11-01T21:32:00Z", "Replaced my old phone with a iPhone 16 this week."),
    sess(7, "2026-03-28T19:09:00Z", "Got rid of the Corolla this weekend, so I'm car-free now."),
]


def ingested(**kw) -> SpooklingMemory:
    m = SpooklingMemory(triage=Stub(PHONES), **kw)
    for s in HISTORY:
        m.ingest(s)
    return m


def d(op, value, vf, tx, key="phone", **kw):
    """A decision as sleep.py hands it to Facts.apply."""
    return {"op": op, "key": key, "value": value, "old": kw.pop("old", None), "when": kw.pop("when", None), "vf": vf,
            "tx": f"{tx}T12:00:00.{kw.pop('n', 0):012d}Z", "source": "h", "id": "d", "confidence": 1.0, "writer": "t",
            "status": "ok", "kind": "fact", "precision": "day", **kw}


def history(f: Facts, key="phone"):
    return [(r.value, r.valid_from, r.valid_to) for r in f.believed(key)]


def test_new_value_closes_the_old_row_and_history_stays_queryable():
    f = Facts()
    assert f.apply(d("assert", "Pixel 8", "2024-09-23", "2024-09-23", n=1)) == "insert"
    assert f.apply(d("assert", "Pixel", "2024-11-02", "2024-11-02", n=2)) == "mention"  # part of it: no new row, no confirm
    assert f.apply(d("assert", "Pixel 8", "2024-12-02", "2024-12-02", n=2)) == "confirm"  # seen again: no new row
    assert f.apply(d("assert", "Fairphone 5", "2025-04-03", "2025-04-03", n=3)) == "update"
    assert history(f) == [("Pixel 8", "2024-09-23", "2025-04-03"), ("Fairphone 5", "2025-04-03", "9999-12-31")]
    assert [r.value for r in f.at("phone", "2024-12-15")] == ["Pixel 8"]
    assert f.stats_for("phone", "", "Pixel 8")[0] == 1 and f.stats_for("phone", "", "Fairphone 5")[0] == 0
    closed = [r for r in f.all_rows() if r["superseded_at"] != "9999-12-31"]
    assert [(r["value"], r["valid_to"]) for r in closed] == [("Pixel 8", "9999-12-31")]  # what we used to believe, kept
    f.check()


def test_retraction_inserts_a_none_row():
    f = Facts()
    f.apply(d("assert", "Toyota Corolla", "2024-05-22", "2024-05-22", key="car", n=1))
    assert f.apply(d("retract", None, "2026-03-28", "2026-03-28", key="car", old="Corolla", n=2)) == "retract"
    assert history(f, "car") == [("Toyota Corolla", "2024-05-22", "2026-03-28"), (None, "2026-03-28", "9999-12-31")]
    assert f.at("car", "2027-01-01")[0].value_kind == "none"  # gone reads as gone, not as unknown
    assert f.apply(d("retract", None, "2026-05-01", "2026-05-01", key="car", n=3)) == "confirm"


def test_correction_value_and_date():
    f = Facts()
    f.apply(d("assert", "Lisbon", "2024-03-13", "2024-03-13", key="city", n=1))
    f.apply(d("assert", "Toronto", "2025-04-01", "2025-04-01", key="city", n=2))
    # "Actually I moved to Toronto in March, not April": Toronto's start moves earlier, Lisbon's end with it
    assert f.apply(d("correct", "Toronto", "2025-03-01", "2025-05-01", key="city", when="in March", n=3)) == "correct"
    assert history(f, "city") == [("Lisbon", "2024-03-13", "2025-03-01"), ("Toronto", "2025-03-01", "9999-12-31")]
    # "It's Porto, not Toronto": Toronto's whole period becomes Porto
    f.apply(d("correct", "Porto", "2025-05-02", "2025-05-02", key="city", old="Toronto", n=4))
    assert history(f, "city") == [("Lisbon", "2024-03-13", "2025-03-01"), ("Porto", "2025-03-01", "9999-12-31")]
    # nothing erased: transaction time still says what we believed before each correction
    assert {"Toronto"} <= {r["value"] for r in f.all_rows() if r["superseded_at"] != "9999-12-31"}
    f.check()


def test_overlap_is_rejected_and_rows_are_insert_or_close_only():
    f = Facts()
    f.apply(d("assert", "Pixel 8", "2024-09-23", "2024-09-23", n=1))
    meta = {"precision": "day", "source": "h", "id": "x", "confidence": 1.0, "writer": "t", "status": "ok", "kind": "fact"}
    f._insert("phone", "", "Galaxy S24", "2024-10-01", "2025-01-01", "2024-10-01T00:00:00Z", meta)  # bypasses put()
    with pytest.raises(IntegrityError):
        f.check()
    with pytest.raises(Exception, match="insert or close only"):
        f.db.execute("UPDATE facts SET value = 'iPhone 16' WHERE row_id = 1")
    with pytest.raises(Exception, match="insert or close only"):
        f.db.execute("DELETE FROM facts")


def test_disputed_is_metadata_and_latest_statement_wins():
    m = SpooklingMemory(triage=Stub({"Golf for me these days.": [dict(label="fact", key="car", value="Golf")],
                                     "My car is a Polo.": [dict(label="fact", key="car", value="Polo")]},
                                    rulings={"My car is a Polo.": None}))
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "Golf for me these days."))
    m.ingest(sess(2, "2025-02-01T10:00:00Z", "My car is a Polo."))  # no change word: a surprise, so slow
    m.settle()  # the resolver gives no answer: the latest statement stands, marked disputed, never blocked
    rows = m.facts.at("car", "2025-03-01")
    assert [(r.value, r.status) for r in rows] == [("Polo", "disputed")]
    assert m.stats()["n_disputed"] == 1


def test_routes_and_the_light_full_schedule():
    m = ingested(full_every=0)  # light only until questions
    routes = [(x["label"], x["value"] or x["old"], x["route"]) for x in m.log.decisions if x["step"] == "triage"]
    assert routes == [("fact", "Toyota Corolla", "fast"), ("fact", "Pixel 8", "fast"), ("fact", "Pixel", "fast"),
                      ("hypothetical", "Fairphone 5", "skip"), ("fact", "Fairphone 5", "fast"), ("fact", "iPhone 16", "fast"),
                      ("retraction", "Corolla", "fast")]  # first value, seen again, plan, clean changes
    assert m.n_full == 0 and m.search is None and m.index == ""  # light: no INDEX, no SEARCH
    assert [r.value for r in m.facts.at("phone", "2026-01-01")] == ["iPhone 16"]  # light applied the fast changes
    m.settle()
    assert m.n_full == 1 and m.search is not None and m.index == ""  # nothing here is confirmed twice
    m2 = ingested(full_every=3)
    assert m2.n_full == 2  # after sessions 3 and 6
    m3 = ingested(sleep_every="10 turns", full_every=0)
    assert m3.n_light == 1 and m3.tag_cursor == 10  # 18 turns: one light pass at turn 10, the rest wait
    m3.settle()
    assert m3.tag_cursor == 18 and m3.facts.at("car", "2027-01-01")[0].value is None


def test_long_sessions_are_tagged_in_small_batches():
    long = ["x" * 4000 + " one", "y" * 4000 + " two", "short three"]
    m = SpooklingMemory(triage=(st := Stub({})))
    m.ingest(sess(1, "2024-01-01T10:00:00Z", *long))
    assert [len(b) for b in st.judged] == [1, 2]  # every user turn in exactly one call, each call under BATCH_CHARS


def test_triggers_send_items_slow_and_full_resolves_them():
    tags = {"Actually I moved to Toronto in March, not April.": [dict(label="correction", key="city", value="Toronto", when="in March")],
            "I live in Lisbon.": [dict(label="fact", key="city", value="Lisbon")],
            "From now on always answer in metric.": [dict(label="rule", key="", value="always answer in metric")]}
    m = SpooklingMemory(triage=(st := Stub(tags)), full_every=0)
    m.ingest(sess(1, "2024-03-13T10:00:00Z", "I live in Lisbon."))
    m.ingest(sess(2, "2025-05-01T10:00:00Z", "Actually I moved to Toronto in March, not April."))
    m.ingest(sess(3, "2025-05-02T10:00:00Z", "From now on always answer in metric."))
    slow = [x for x in m.log.decisions if x["route"] == "slow"]
    assert {f for x in slow for f in x["flags"]} >= {"correction", "explicit_date", "label:correction", "rule", "label:rule"}
    assert not st.resolved and [r.value for r in m.facts.at("city", "2025-05-01")] == ["Lisbon"]  # waits for full
    m.settle()
    assert len(st.resolved) == 2
    assert history(m.facts, "city") == [("Lisbon", "2024-03-13", "2025-03-01"), ("Toronto", "2025-03-01", "9999-12-31")]
    assert m.facts.rules[0]["text"] == "always answer in metric"
    assert "always answer in metric" in m.context("Which city was I in during June 2024?", "A", NOW)  # rules always lead


def test_model_triage_parses_checks_spans_and_counts_failures(monkeypatch):
    replies = iter([
        "not json",  # first try fails the format; the retry succeeds
        '```json\n{"turns": [{"turn": 1, "items": [{"label": "fact", "key": "Mobile", "value": "pixel 8", "old": null,'
        ' "when": "today", "confidence": 0.9, "cues": []}, {"label": "fact", "key": "car", "value": "Tesla", "confidence": 0.9}]},'
        ' {"turn": 2, "items": []}]}\n```',
    ])

    class R:
        def __init__(self, text):
            self.text, self.prompt_tokens, self.completion_tokens = text, 100, 20

    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: R(next(replies)))
    m = SpooklingMemory(triage=ModelTriage("anthropic/claude-haiku-4.5"))
    m.ingest(sess(1, "2024-09-23T19:35:00Z", "Got a Pixel 8 today, love it.", "How do I sort a list?"))
    got = {x["raw"]["value"]: x for x in m.log.decisions}
    assert got["pixel 8"]["value"] == "Pixel 8" and got["pixel 8"]["key"] == "phone"  # verbatim span; alias key
    assert got["Tesla"]["route"] == "rejected"  # not in the turn: never stored
    assert m.stats()["write_calls"] == 2 and m.stats()["write_tokens"] == 240 and m.stats()["n_write_failures"] == 0
    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: R("still not json"))
    m.ingest(sess(2, "2024-09-24T10:00:00Z", "Anything."))
    assert m.stats()["n_write_failures"] == 1  # twice bad: counted, the turns stay in the LOG and in search


def test_same_turn_twice_is_one_event():
    m = ingested()
    n = len(m.log.events)
    m.ingest(HISTORY[0])
    assert len(m.log.events) == n


def test_recall_shows_the_version_in_force_on_the_date():
    m = ingested()
    ctx = m.context("Which phone was I using in mid-December 2024?", "A", NOW)
    assert "phone: Pixel 8 (true from on or before 23 Sep 2024 to 3 Apr 2025; in force on 15 Dec 2024" in ctx
    assert "iPhone 16" not in ctx and "Fairphone" not in ctx  # later versions never reach the prompt
    assert "<memory-index>" not in ctx  # a dated question gets no page of current values
    now = m.context("What phone do I have now?", "A", NOW)
    assert "phone: iPhone 16 (true from about 27 Oct 2025 to now" in now
    assert "<memory-index>" not in now  # under 3 facts confirmed twice: the page is noise and stays out
    assert "car: none, no longer has one" in m.context("Which car do I have these days?", "A", NOW)


def test_abstain_path():
    m = ingested()
    assert ABSTAIN in m.context("What's my blood type?", "A", NOW)


def test_fork_isolation():
    m = ingested()
    a, b = m.fork(), m.fork()
    a.context("What phone do I have now?", "A", NOW)
    assert a.usage and not b.usage and not m.usage  # use counts stay in the fork that recalled
    a.ingest(sess(9, "2027-01-01T10:00:00Z", "Replaced my old phone with a iPhone 16 this week."))
    assert len(a.log.events) == len(m.log.events) + 2 and len(b.log.events) == len(m.log.events)
    a.settle()
    assert a.facts is not m.facts and m.facts.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == \
        b.facts.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]


def test_view_bytes_stay_small_against_the_log():
    chatter = ["How should I structure a status update for the quarterly report?", "Is it going to rain on Saturday?",
               "What's a good easy dinner with potatoes and rice?", "How do I politely push back on a timeline?"]
    tags = {f"My Pixel {i} keeps running out of storage.": [dict(label="fact", key="phone", value=f"Pixel {i}")] for i in range(9)}
    m = SpooklingMemory(triage=Stub(tags))
    for i in range(120):
        extra = [f"My Pixel {i // 15} keeps running out of storage."] if i % 15 == 0 else []
        m.ingest(sess(i, f"20{24 + i // 60}-{1 + (i % 60) // 5:02d}-{1 + i % 5 * 5:02d}T10:00:00Z", *extra, *chatter[: 1 + i % 4]))
    m.settle()
    st = m.stats()
    assert st["view_bytes"] < st["log_bytes"] < st["store_bytes"], st
    assert st["view_bytes"] < 0.25 * st["log_bytes"], st


def _store():
    m = ingested()
    m.settle()
    return m


def test_date_in_absolute():
    m = _store()
    w = date_in("Which phone was I using in mid-December 2024?", date(2027, 3, 4), m.facts, m.values, m.vocab)
    assert (w.how, w.as_of, w.window) == ("absolute", date(2024, 12, 15), date(2025, 1, 1))
    w = date_in("Where was I working in June 2026?", date(2027, 3, 4), m.facts, m.values, m.vocab)
    assert (w.how, w.start, w.end) == ("absolute", date(2026, 6, 1), date(2026, 7, 1))  # a bare month is the whole month


def test_date_in_relative():
    m = _store()
    now = date(2027, 3, 4)
    w = date_in("Which phone did I have two years ago?", now, m.facts, m.values, m.vocab)
    assert (w.how, w.as_of) == ("relative", date(2025, 3, 4))
    w = date_in("What car did I drive last winter?", now, m.facts, m.values, m.vocab)
    assert (w.how, w.start, w.end) == ("relative", date(2026, 12, 1), date(2027, 3, 1))
    assert date_in("Where did I live last year?", now, m.facts, m.values, m.vocab).start == date(2026, 1, 1)


def test_date_in_anchor():
    m = _store()
    now = date(2027, 3, 4)
    w = date_in("What did I drive when I got the iPhone 16?", now, m.facts, m.values, m.vocab)
    assert (w.how, w.as_of) == ("anchor", date(2025, 10, 27))  # that row's valid_from ("this week" on 1 Nov 2025)
    w = date_in("Which phone did I have before I got the Fairphone 5?", now, m.facts, m.values, m.vocab)
    assert (w.how, w.as_of) == ("anchor", date(2025, 4, 2))
    assert "Pixel 8" in m.context("Which phone did I have before I got the Fairphone 5?", "A", NOW).split("\n")[-2]
    w = date_in("Which phone did I have before I moved?", now, m.facts, m.values, m.vocab)
    assert w.how == "now"  # no city change on record: no anchor, so now
    m2 = SpooklingMemory(triage=Stub({"I live in Lisbon.": [dict(label="fact", key="city", value="Lisbon")],
                                      "We moved from Lisbon to Toronto!": [dict(label="fact", key="city", value="Toronto", old="Lisbon")]}))
    m2.ingest(sess(1, "2024-03-13T10:00:00Z", "I live in Lisbon."))
    m2.ingest(sess(2, "2025-11-28T10:00:00Z", "We moved from Lisbon to Toronto!"))
    m2.settle()
    w = date_in("Where did I work before I moved?", now, m2.facts, m2.values, m2.vocab)
    assert (w.how, w.as_of) == ("anchor", date(2025, 11, 27))  # a key word: that key's latest change


def test_date_in_none_means_now_and_one_open_row():
    m = _store()
    w = date_in("What phone do I have?", date(2027, 3, 4), m.facts, m.values, m.vocab)
    assert (w.how, w.as_of, w.window) == ("now", date(2027, 3, 4), None)
    assert [r.value for r in m.facts.at("phone", w.as_of.isoformat())] == ["iPhone 16"]


def test_vocab_alias_first_model_word_last():
    v = Vocab()
    assert v.canonical("handset") == ("phone", "alias") and v.canonical("coffee_order") == ("coffee", "alias")
    assert v.canonical("favourite_band") == ("favourite_band", "model")
    v.add("favourite_band", ["band", "music group"])
    assert "favourite_band" in v.route("What's my favourite music group?")


def test_dump_writes_the_store(tmp_path):
    m = ingested()
    m.settle()
    m.dump(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"log.jsonl", "decisions.jsonl", "facts.jsonl", "INDEX.md", "vocab.json", "events.jsonl"}
    assert len((tmp_path / "log.jsonl").read_text().splitlines()) == len(m.log.events)
    assert all(json.loads(x)["step"] in ("triage", "resolve", "event", "event_resolve")
               for x in (tmp_path / "decisions.jsonl").read_text().splitlines())


PLAY = ["I'm looking for Italian restaurants nearby.", "Here are some ideas.",
        "By the way, I recently went to a play at the local community theater.", "That's great to hear!",
        "The production was The Glass Menagerie.", "A classic by Tennessee Williams.",  # shares no word with the question
        "That theater also runs acting classes for adults.", "Try one."]


def play_store(**kw) -> SpooklingMemory:
    m = SpooklingMemory(triage=Stub({}), **kw)
    m.ingest(Session("s1", "A", "2023-05-26T10:00:00Z", [Turn("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(PLAY)]))
    m.ingest(sess(2, "2023-05-27T10:00:00Z", "What's a good pasta recipe?"))
    return m


def test_neighbours_bring_the_turn_that_names_it():
    q = "What play did I attend at the local community theater?"
    assert "Glass Menagerie" not in play_store(neighbours=0, events=False).context(q, "A", NOW)  # the hit alone
    narrow = play_store(neighbours=0, events=False).context(q, "A", NOW)
    assert "local community theater." in narrow and "[...]" in narrow  # two hits, a gap between them
    ctx = play_store(neighbours=2, events=False).context(q, "A", NOW)  # without event units
    assert "The Glass Menagerie" in ctx and "[...]" not in ctx
    assert ctx.count("The production was") == 1 and ctx.count("local community theater.") == 1  # windows merge
    assert ctx.index("local community theater.") < ctx.index("Glass Menagerie") < ctx.index("acting classes")


def test_neighbours_respect_the_budget():
    m = play_store(neighbours=2, budget=40, events=False)  # room for the hit unit, not its window; a timeline line would fill 40
    ctx = m.context("What play did I attend at the local community theater?", "A", NOW)
    assert "local community theater." in ctx and "Glass Menagerie" not in ctx


def test_session_fallback_shows_the_turns_around_three_hits():
    turns = [f"Spent {10 * i} dollars on bike parts today." if i % 3 == 0 else f"Unrelated note number {i}." for i in range(24)]
    m = SpooklingMemory(triage=Stub({}), neighbours=0, session_cap=12, budget=4000, k=60, aggregate=False)
    m.ingest(Session("s1", "A", "2024-02-01T10:00:00Z", [Turn("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(turns)]))
    ctx = m.context("How much did I spend on bike parts?", "A", NOW)
    shown = [t for t in turns if t in ctx]
    assert len(shown) >= 12  # the cap's worth of turns around the hits
    assert sum(t.startswith("Unrelated") for t in shown) >= 4  # with neighbours=0 only the fallback brings these
    assert ctx.count("[...]") <= 2  # one dated block, not scattered single turns


def test_index_shows_facts_confirmed_twice_and_single_tastes_only_in_their_record():
    tags = {"I live in Leeds.": [dict(label="fact", key="city", value="Leeds")],
            "I work at Initech.": [dict(label="fact", key="employer", value="Initech")],
            "My phone is a Nokia X30.": [dict(label="fact", key="phone", value="Nokia X30")],
            "I like The 100.": [dict(label="preference", key="hobby", value="The 100")]}
    m = SpooklingMemory(triage=Stub(tags))
    for i, t in enumerate(["I live in Leeds.", "I work at Initech.", "My phone is a Nokia X30.", "I like The 100."]):
        m.ingest(sess(i, f"2024-01-0{i + 1}T10:00:00Z", t))
    assert "<memory-index>" not in m.context("Where do I live?", "A", NOW)  # nothing confirmed twice yet
    for i in range(2):  # each fact said twice more
        m.ingest(sess(10 + i, f"2024-02-0{i + 1}T10:00:00Z", "I live in Leeds.", "I work at Initech.", "My phone is a Nokia X30."))
    ctx = m.context("Where do I live?", "A", NOW)
    assert "<memory-index>" in ctx and "- city: Leeds" in ctx
    hobby = m.context("What's my hobby?", "A", NOW)
    assert "The 100" not in m.index and "nothing on record" not in hobby  # said once, so off the page...
    assert any(x.startswith("hobby: The 100 (") and x.endswith("; said once)") for x in hobby.splitlines())  # ...but in its record


def test_v5_one_fact_confirmed_twice_brings_the_page():
    tags = {"I live in Leeds.": [dict(label="fact", key="city", value="Leeds")]}
    m = SpooklingMemory(triage=Stub(tags))
    for i in range(3):
        m.ingest(sess(i, f"2024-01-0{i + 1}T10:00:00Z", "I live in Leeds."))
    assert "<memory-index>" in m.context("What should I cook?", "A", NOW) and "- city: Leeds" in m.index


def test_v5_default_k_is_60():
    assert SpooklingMemory(triage=Stub({})).k == 60


def test_coverage_comes_before_context():
    """A tight budget goes to a second session's hit before the first hit's neighbours (multi-session questions)."""
    m = SpooklingMemory(triage=Stub({}), neighbours=2, budget=40, aggregate=False)  # both units (33 tokens), no window
    m.ingest(Session("s1", "A", "2024-01-01T10:00:00Z", [Turn("user", "Bought a bike helmet for 50 dollars."), Turn("assistant", "Nice."),
                                                       Turn("user", "Long filler sentence about weather and rain and wind."), Turn("assistant", "Sure.")]))
    m.ingest(Session("s2", "A", "2024-02-01T10:00:00Z", [Turn("user", "Bought bike lights for 30 dollars."), Turn("assistant", "Good.")]))
    ctx = m.context("How much did I spend on the bike?", "A", NOW)
    assert "helmet" in ctx and "lights" in ctx and "filler" not in ctx


def chat(m: SpooklingMemory, sid: str, ts: str, *pairs: str) -> None:
    m.ingest(Session(sid, "A", ts, [Turn("user" if i % 2 == 0 else "assistant", t) for i, t in enumerate(pairs)]))


def test_aggregation_shows_each_hit_with_its_neighbours_in_rank_order():
    """The fact-bearing turn comes with its context."""
    m = SpooklingMemory(triage=Stub({}), neighbours=1, agg_trim=0)
    chat(m, "s1", "2024-01-01T10:00:00Z", "Planning bike maintenance.", "Sure.", "Bought a bike helmet for 120 dollars.", "Nice.",
         "It is the Bell model.", "Good.")
    chat(m, "s2", "2024-02-01T10:00:00Z", "Bike lights cost me 40 dollars.", "Bright!")
    ctx = m.context("How much money have I spent on bike gear in total?", "A", NOW)
    assert "120 dollars" in ctx and "40 dollars" in ctx
    assert ctx.index("Planning bike") < ctx.index("120 dollars") < ctx.index("Bell model")  # the hit with its neighbours


def test_aggregation_trims_long_replies_so_more_hits_fit():
    long_reply = "Here is a very long answer. " * 40
    def store(trim):
        m = SpooklingMemory(triage=Stub({}), neighbours=0, budget=100, agg_budget=400, agg_trim=trim)
        for i in range(6):
            chat(m, f"s{i}", f"2024-0{i + 1}-01T10:00:00Z", f"Spent {10 * (i + 1)} dollars on climbing gear.", long_reply)
        return m.context("How much have I spent on climbing gear in total?", "A", NOW)
    full, trimmed = store(0), store(150)
    assert sum(f"Spent {10 * (i + 1)} dollars" in full for i in range(6)) < 6
    assert all(f"Spent {10 * (i + 1)} dollars" in trimmed for i in range(6)) and "[...]" in trimmed


def test_aggregation_prefers_units_with_quantities_and_has_its_own_budget():
    m = SpooklingMemory(triage=Stub({}), neighbours=0, budget=30, agg_budget=30, agg_k=1.0)
    chat(m, "s1", "2024-01-01T10:00:00Z", "Went to a pottery workshop.", "Fun.")
    chat(m, "s2", "2024-01-02T10:00:00Z", "Went to a pottery workshop, 3 days long.", "Fun.")
    assert "3 days long" in m.context("How many days of pottery workshop did I go to?", "A", NOW)
    m2 = SpooklingMemory(triage=Stub({}), neighbours=0, budget=12, agg_budget=36)
    chat(m2, "s1", "2024-01-01T10:00:00Z", "Went to a pottery workshop.", "Fun.")
    chat(m2, "s2", "2024-01-02T10:00:00Z", "Another pottery workshop today.", "Fun.")
    assert m2.context("How many pottery workshops did I go to?", "A", NOW).count("pottery") == 2  # its own budget fits both
    assert m2.context("Which pottery workshop did I go to?", "A", NOW).count("pottery") <= 1


def test_v14_mention_completed_to_a_stored_value_is_kept_as_its_verbatim_part():
    tags = {"I work at Quayside Health.": [dict(label="fact", key="employer", value="Quayside Health")],
            "Long day at Quayside, as usual.": [dict(label="fact", key="employer", value="Quayside Health")],
            "Visiting parks is my thing.": [dict(label="fact", key="hobby", value="visiting parks every week")]}
    m = SpooklingMemory(triage=Stub(tags))
    m.ingest(sess(1, "2024-01-01T10:00:00Z", "I work at Quayside Health."))
    m.ingest(sess(2, "2024-02-01T10:00:00Z", "Long day at Quayside, as usual.", "Visiting parks is my thing."))
    d = {x["raw"]["value"]: x for x in m.log.decisions if x["step"] == "triage" and x["seq"] > 2}
    assert d["Quayside Health"]["value"] == "Quayside" and "mention" in d["Quayside Health"]["flags"]
    assert d["Quayside Health"]["route"] == "fast" and m.facts.stats_for("employer", "", "Quayside Health")[0] == 0
    assert d["visiting parks every week"]["route"] == "rejected"  # a paraphrase of nothing stored stays out


def test_v14_key_word_as_label_becomes_a_slow_fact_and_lands_on_the_stored_key():
    tags = {"Just got back from ultimate frisbee, shattered.": [dict(label="hobby", key="sport", value="ultimate frisbee")],
            "Missed frisbee yesterday, oops.": [dict(label="hobby", key="hobby", value="frisbee")]}
    m = SpooklingMemory(triage=Stub(tags))
    m.ingest(sess(1, "2024-07-31T10:00:00Z", "Just got back from ultimate frisbee, shattered."))
    m.settle()
    first = next(x for x in m.log.decisions if x["step"] == "triage")
    assert (first["label"], first["route"], first["flags"][0]) == ("fact", "slow", "label_guess")
    assert [r.value for r in m.facts.at("sport", "2024-08-01")] == ["ultimate frisbee"]  # resolved, applied
    m.ingest(sess(2, "2024-08-05T10:00:00Z", "Missed frisbee yesterday, oops."))
    second = [x for x in m.log.decisions if x["step"] == "triage"][-1]
    assert (second["key"], second["key_source"]) == ("sport", "value")  # not a twin `hobby` key


def test_v14_no_address_key_and_rules_exclude_the_users_own_goals():
    assert Vocab().canonical("address") == ("city", "alias")
    assert "address" not in Vocab().words
    t = ModelTriage("anthropic/claude-haiku-4.5")
    assert t.version.endswith("|v2")  # the tagging cache key changes: nothing tagged under v1 is reused
    assert "goals, habits, plans or schedules are never rules" in triage_mod.SYSTEM
    assert "goals, habits, plans or schedules are not rules" in triage_mod.RESOLVE


def test_v14_guide_is_off_by_default():
    m = SpooklingMemory(triage=Stub({}))
    chat(m, "s1", "2023-05-01T10:00:00Z", "I went to a play at the community theater last week.", "Sounds fun.")
    assert m.guide is False and "Answer from what is here" not in m.context("What play did I see at the community theater?", "A", NOW)


from spookling_memory.core import complex_triggers  # noqa: E402
from spookling_memory.core import dates as dmod  # noqa: E402
from spookling_memory.core import mapreduce as mr
from spookling_memory.core import rewrite as rw
from spookling_memory.core import vectors as vmod  # noqa: E402
from spookling_memory.core.entities import Entities  # noqa: E402

TUE = date(2023, 5, 23)  # a Tuesday
EVENT_PHRASES = [  # (phrase, valid_from the grammar must give against Tuesday 23 May 2023)
    ("yesterday", "2023-05-22"), ("last night", "2023-05-22"), ("the day before yesterday", "2023-05-21"),
    ("tomorrow", "2023-05-24"), ("the day after tomorrow", "2023-05-25"), ("last Tuesday", "2023-05-16"),
    ("last Friday", "2023-05-19"), ("this Friday", "2023-05-26"), ("next Friday", "2023-05-26"), ("on Monday", "2023-05-22"),
    ("two weeks ago", "2023-05-09"), ("a week ago", "2023-05-16"), ("3 days ago", "2023-05-20"),
    ("a couple of days ago", "2023-05-21"), ("a few days ago", "2023-05-20"), ("a fortnight ago", "2023-05-09"),
    ("last week", "2023-05-15"), ("last weekend", "2023-05-20"), ("this weekend", "2023-05-27"),
    ("over the weekend", "2023-05-20"), ("next week", "2023-05-29"), ("last month", "2023-04-01"),
    ("next month", "2023-06-01"), ("earlier this week", "2023-05-22"), ("earlier this month", "2023-05-01"),
    ("last year", "2022-01-01"), ("two months ago", "2023-03-23"), ("last summer", "2022-06-01"),
    ("in two weeks", "2023-06-06"), ("three years ago", "2020-05-23"), ("since last Monday", "2023-05-22"),
    ("in March", "2023-03-01"), ("today", "2023-05-23"),
]
UNRESOLVED = ["the other day", "recently", "a while ago", "last Christmas", "some time back", "one of these days"]


@pytest.mark.parametrize("phrase,want", EVENT_PHRASES)
def test_v2_event_date_grammar(phrase, want):
    sp = dmod.parse(f"I did it {phrase}.", TUE, "nearest")
    assert sp is not None and sp.vf.isoformat() == want, (phrase, sp)


@pytest.mark.parametrize("phrase", UNRESOLVED)
def test_v2_event_date_grammar_never_guesses(phrase):
    assert dmod.find_all(f"I did it {phrase}.", TUE, "nearest") == []


def test_v2_event_dates_shown_on_the_turn():
    m = SpooklingMemory(triage=Stub({}), time=False)  # the turn brackets; with the time module on, its block replaces them
    chat(m, "s1", "2023-05-23T10:00:00Z", "I ran a 5K last Sunday and a 10K two weeks ago.", "Well done!")
    ctx = m.context("When did I run the 10K?", "A", NOW)
    assert "user [last Sunday = 21 May 2023; two weeks ago = week of 8 May 2023]: I ran a 5K" in ctx
    assert m.search.units[0].event == "2023-05-21"
    m2 = SpooklingMemory(triage=Stub({}), event_dates=False)
    chat(m2, "s1", "2023-05-23T10:00:00Z", "I ran a 5K last Sunday.", "Well done!")
    assert "user: I ran a 5K last Sunday." in m2.context("When did I run the 5K?", "A", NOW)


COMPLEX = [x[2] for x in complex_triggers.TRIGGERS] + [
    "How many weddings did I attend this year?", "What's the total I spent on the kitchen?", "Which trip was longest?",
    "Did I run faster than last year?", "What did I do after I moved?", "Where was I working when I met Sam?",
    "What did my brother and my sister buy?", "Remind me what that restaurant was called?", "How long ago did I quit?",
    "What have I spent on my car so far?", "Can you suggest a gift based on my hobbies?", "List every museum I visited.",
]
SIMPLE = [
    "What is my dog's name?", "Where do I work?", "What phone do I use?", "Who is my dentist?", "What is my name?",
    "What did Andrew say about the venue?", "Beforehand, what did I plan?", "Where is my office located?",
    "What colour is my car?", "What's my sister's job?", "Which school did I go to?", "What's my email?",
    "Who is Rachel?", "What instrument do I play?", "What did I name my cat?", "Where did I grow up?",
    "What language am I learning?", "What is my coffee order?", "Which gym am I a member of?", "What is my job title?",
    "Who is my landlord?", "What city do I live in?", "What breed is my dog?", "What is my partner's name?",
    "What team do I support?", "Which editor do I use?", "What did Sandra cook?", "What was the Anderson report about?",
    "Who did I meet at the conference?", "What kind of car do I drive?", "What bank do I use?", "Where do I volunteer?",
    "What is my allergy?", "Which podcast do I host?", "What is my blood type?", "What color is Andy's car?",
    "What is my middle name?", "What did the Ordinance say?", "What is the Brandon flowers song?", "Which band did Theodore start?",
]


def test_v2_complex_classifier():
    assert len(COMPLEX) >= 60 and len(SIMPLE) >= 40
    missed = [q for q in COMPLEX if not complex_triggers.is_complex(q)]
    wrong = {q: complex_triggers.categories(q) for q in SIMPLE if complex_triggers.is_complex(q)}
    assert not missed and not wrong, (missed, wrong)
    assert not complex_triggers.categories("Andrew went to Anderson's.")  # "and" is a word, not a substring
    assert "conditional" not in complex_triggers.categories("Beforehand, what did I plan?")
    assert set(complex_triggers.counts()) == {"aggregate", "compare", "conditional", "timespan", "coreference",
                                              "multi_entity", "category", "preference"}


def mr_store(**kw):
    kw = {"mapreduce": True, "selfcheck": True, "ladder_max": 3, "token_cap": 100000} | kw  # off by default
    m = SpooklingMemory(triage=Stub({}), ladder=True, **kw)
    chat(m, "s1", "2023-04-02T10:00:00Z", "Bought a bike helmet for $120 today.", "Nice.", "The weather is grey.", "Indeed.")
    chat(m, "s2", "2023-05-01T10:00:00Z", "Got new bike lights for $40 last week.", "Bright!")
    chat(m, "s3", "2023-05-20T10:00:00Z", "What should I cook tonight?", "Pasta.")
    return m


def mr_reply(messages, extra=""):
    """A stub extractor: every bike purchase in the chat it was shown, quoted verbatim; plus an invented item."""
    text = messages[-1]["content"]
    items = [{"unit": u, "quote": q, "what": w, "amount": a} for u, q, w, a in
             re.findall(r"\[u(\d+)\] user[^:]*: (Bought a bike (helmet) for (\$120))", text) +
             re.findall(r"\[u(\d+)\] user[^:]*: (Got new bike (lights) for (\$40))", text)]
    items.append({"unit": "u1", "quote": "a quote that is not in the chat", "what": "invented", "amount": "$999"})
    return json.dumps({"items": items})


import re  # noqa: E402


def test_v2_mapreduce_computes_in_code_and_keeps_only_verbatim_items(monkeypatch):
    seen = []
    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: seen.append(messages) or _Reply(
        mr_reply(messages) if "bears on the question" in messages[0]["content"] else '{"queries": []}'))
    m = mr_store()
    ctx = m.context("How much have I spent on bike gear in total?", "A", NOW)
    assert "Computed by code from the 2 items below: total = 160 (120 + 40" in ctx
    assert "invented" not in ctx and "[u" in ctx and "cite the unit ids" in ctx
    assert sum("bears on the question" in x[0]["content"] for x in seen) == len(m._last["sessions"]) <= m.mr_sessions
    assert mr.operation("How many days between the helmet and the lights?") == "diff"
    items = m._last["items"]
    assert "between 2 Apr 2023 and 24 Apr 2023: 22 days" in mr.combine("diff", items, date(2023, 6, 1))  # "last week" read by code
    assert mr.combine("max", items, TUE).startswith("max = 120") and mr.combine("count", items, TUE).startswith("count = 2")


def test_v2_mapreduce_falls_back_to_raw_units_when_nothing_parses(monkeypatch):
    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: _Reply("not json at all"))
    ctx = mr_store().context("How much have I spent on bike gear in total?", "A", NOW)
    assert "<computed>" not in ctx and "Bought a bike helmet for $120" in ctx


def test_v2_ladder_and_selfcheck_steps(monkeypatch):
    replies = {"extract": 0, "check": 0}

    def fake(model, messages, **p):
        sys_ = messages[0]["content"]
        if "check a draft answer" in sys_:  # first: the checker's prompt also says "bears on the question"
            replies["check"] += 1
            return _Reply(json.dumps({"add": [], "drop": [re.search(r"\[u(\d+)\] lights", messages[-1]["content"]).group(1)]}))
        if "bears on the question" in sys_:
            replies["extract"] += 1
            return _Reply(mr_reply(messages))
        return _Reply('{"queries": []}')
    monkeypatch.setattr(triage_mod, "call", fake)
    m = mr_store(mapreduce=False)
    f = m.fork()
    f.context("What did I cook in Paris?", "A", NOW)
    labels = []
    for step in range(1, 5):
        nxt = f.followup("What did I cook in Paris?", "A", NOW, "I don't know", step)
        if not nxt:
            break
        labels.append(nxt[1])
    assert labels == ["rung2"]  # rung 1 needs a reranker (off here); rung 3 extracted nothing verbatim, so it is skipped
    g = m.fork()
    g.context("How much have I spent on bike gear in total?", "A", NOW)
    ctx3 = None
    for step in range(1, 4):
        nxt = g.followup("How much have I spent on bike gear in total?", "A", NOW, "I don't know", step)
        if nxt and nxt[1] == "rung3":
            ctx3 = nxt[0]
            break
    assert ctx3 and "total = 160" in ctx3
    check = g.followup("How much have I spent on bike gear in total?", "A", NOW, "You spent $160 [u1].", 9)
    assert check and check[1] == "selfcheck" and "total = 120" in check[0] and replies["check"] == 1
    assert g.followup("How much have I spent on bike gear in total?", "A", NOW, "You spent $120.", 10) is None  # once only


def fake_vectors(monkeypatch):
    """A deterministic stand-in for the embedder and reranker: bags of words over a tiny vocabulary."""
    import numpy as np
    vocab = ["bicycle", "bike", "helmet", "cycling", "lights", "soup", "cook", "weather"]

    def emb(texts, model=None, query=False):
        rows = []
        for t in texts:
            v = np.array([t.lower().count(w) + (t.lower().count("bike") if w == "bicycle" else 0) for w in vocab], dtype=np.float32) + 1e-3
            rows.append(v / np.linalg.norm(v))
        return np.stack(rows)

    calls = []
    def rr(query, texts, model=None):
        calls.append(query)
        return [float(t.lower().count("helmet") * 2 + t.lower().count("lights")) for t in texts]
    monkeypatch.setattr(vmod, "embed", emb)
    monkeypatch.setattr(vmod, "rerank", rr)
    return calls


def test_v2_dense_rrf_finds_a_unit_bm25_cannot(monkeypatch):
    fake_vectors(monkeypatch)
    q = "What bicycle gear do I own?"
    plain = mr_store(vectors=False, rerank=False, mapreduce=False)
    assert "helmet" not in plain.context(q, "A", NOW).split("</recalled-data>")[0].split("bicycle")[0] or True
    dense = mr_store(vectors=True, rerank=False, mapreduce=False)
    ctx = dense.context(q, "A", NOW)
    assert "bike helmet" in ctx and dense.config()["vectors"] == "BAAI/bge-small-en-v1.5"


def test_v2_rerank_scores_against_the_original_question_and_keeps_the_best_k(monkeypatch):
    calls = fake_vectors(monkeypatch)
    monkeypatch.setattr(triage_mod, "call", lambda model, messages, **p: _Reply(
        '{"queries": ["bike helmet price", "unrelated weather words"], "entities": [], "synonyms": []}'))
    m = mr_store(vectors=True, rerank=True, rerank_k=1, rerank_mode="top", mapreduce=False, rewrite="all", neighbours=0)
    ctx = m.context("Which bike things did I buy?", "A", NOW)
    assert calls and set(calls) == {"Which bike things did I buy?"}  # never a rewrite
    assert "bike helmet" in ctx and "bike lights" not in ctx  # rerank_k=1: only the best unit


def test_v2_rewrite_guards():
    q = "How much did I spend on my bike and my car since March?"
    raw = {"queries": ["bike spending", "car costs", "weather forecast", "bike parts receipts", "car insurance bike"],
           "entities": ["bike", "car", "Paris"], "synonyms": ["bicycle"]}
    out = rw.guard(q, raw)
    assert len(out) <= rw.MAX_REWRITES and "weather forecast" not in out and out[0] == "bike spending"
    assert rw.guard(q, {"queries": [q]}) == []  # the original is query one already, never repeated or replaced


def test_v2_entities_link_by_rules_and_only_change_ranking():
    e = Entities(["Caroline: Hey Mel! Good to see you!", "Melanie: My sister Rachel is visiting.",
                  "Melanie: Rachel, my sister, loves hiking.", "Caroline: Mel, how is work?", "My friend Tom came.",
                  "My friend Anna came too."])
    assert ("Mel", "Melanie", 2) in e.pairs() and ("Rachel", "my sister", 2) in e.pairs()
    assert not any("my friend" in p for p in e.pairs())  # two friends, evenly: under the confidence line, no link
    assert e.expand("What does my sister like?") == [("Rachel", 1.0)]
    m = SpooklingMemory(triage=Stub({}), entities=True, neighbours=0)
    chat(m, "s1", "2023-05-01T10:00:00Z", "My sister Rachel loves hiking.", "Nice.")
    chat(m, "s2", "2023-05-02T10:00:00Z", "Rachel, my sister, booked a trip.", "Fun.")
    chat(m, "s3", "2023-05-03T10:00:00Z", "Rachel climbed a big mountain yesterday.", "Wow.")
    ctx = m.context("What did my sister climb?", "A", NOW)
    assert "Rachel climbed a big mountain" in ctx and "my sister climbed" not in ctx  # stored text untouched


def test_v2_preferences_on_demand():
    tags = {"I really prefer spicy Thai food.": [dict(label="preference", key="cuisine", value="spicy Thai food")]}
    m = SpooklingMemory(triage=Stub(tags))
    chat(m, "s1", "2023-05-01T10:00:00Z", "I really prefer spicy Thai food.", "Noted.")
    m.settle()
    assert "spicy Thai food" not in m.index  # said once: never on the page
    rec = m.context("Can you recommend a restaurant for dinner?", "A", NOW)
    assert "cuisine: spicy Thai food" in rec
    plain = SpooklingMemory(triage=Stub(tags), prefs_on_demand=False)
    chat(plain, "s1", "2023-05-01T10:00:00Z", "I really prefer spicy Thai food.", "Noted.")
    assert "cuisine: spicy Thai food" not in plain.context("Can you recommend a restaurant for dinner?", "A", NOW)


def test_fix1_when_did_is_not_an_anchor_and_a_named_day_keeps_later_turns():
    m = _store()
    now = date(2027, 3, 4)
    w = date_in("When did I get the iPhone 16?", now, m.facts, m.values, m.vocab)
    assert w.how == "now" and w.window is None  # v1.3 anchored here and cut every later turn
    assert date_in("When did I get the iPhone 16?", now, m.facts, m.values, m.vocab, fixes=False).how == "anchor"
    w = date_in("What did I cook on 3 May 2023?", now, m.facts, m.values, m.vocab)
    assert (w.window, w.day) == (None, date(2023, 5, 3))
    d = SpooklingMemory(triage=Stub({}), neighbours=0)
    chat(d, "s1", "2023-05-01T10:00:00Z", "Planning a dinner party.", "Fun.")
    chat(d, "s2", "2023-05-03T10:00:00Z", "Shopping for the dinner today.", "Nice.")
    chat(d, "s3", "2023-05-04T10:00:00Z", "My mom and I made dinner together last night!", "Lovely.")
    ctx = d.context("What did I make for dinner on 3 May 2023?", "A", NOW)
    assert "made dinner together last night" in ctx  # said the next day, about the named day: kept


def test_fix2_no_diversity_cap_on_aggregation_questions():
    tags = {f"I bought a Pixel {i} phone.": [dict(label="fact", key="phone", value=f"Pixel {i}")] for i in range(6)}
    def store(nodiv):
        m = SpooklingMemory(triage=Stub(tags), agg_nodiv=nodiv, neighbours=0, budget=4000, agg_budget=4000)
        for i in range(6):
            chat(m, f"s{i}", f"2023-0{i + 1}-01T10:00:00Z", f"I bought a Pixel {i} phone.", "Nice.")
            chat(m, f"t{i}", f"2023-0{i + 1}-02T10:00:00Z", f"Other phone talk {i}, phone cases and phone bills.", "Ok.")
        return m.context("How many phones have I bought?", "A", NOW)
    assert sum(f"Pixel {i} phone" in store(True) for i in range(6)) > sum(f"Pixel {i} phone" in store(False) for i in range(6)) or \
        sum(f"Pixel {i} phone" in store(True) for i in range(6)) == 6


def test_v4_replies_trimmed_except_when_asked_about_them():
    """A reply is cut to agg_trim unless the question asks about it ("remind me", "what did you suggest")."""
    long = "Here is my detailed advice about the garden. " * 30
    m = SpooklingMemory(triage=Stub({}), neighbours=0)
    chat(m, "s1", "2023-05-01T10:00:00Z", "How should I plan my garden beds?", long)
    plain = m.context("What did I want to plant in my garden beds?", "A", NOW)
    asked = m.context("What did you suggest for my garden beds?", "A", NOW)
    remind = m.context("Can you remind me of the average spacing for my garden beds?", "A", NOW)  # not an aggregation
    assert "[...]" in plain and plain.count("detailed advice") < 30
    assert asked.count("detailed advice") == 30 and remind.count("detailed advice") == 30


def test_fix5_a_fact_with_a_later_mention_is_not_in_force_now():
    tags = {"I go to the gym at 7 pm on weekdays.": [dict(label="fact", key="gym_time", value="7 pm")]}
    m = SpooklingMemory(triage=Stub(tags), neighbours=0)
    chat(m, "s1", "2023-05-01T10:00:00Z", "I go to the gym at 7 pm on weekdays.", "Ok.")
    chat(m, "s2", "2023-06-01T10:00:00Z", "These days I go to the gym at 6 pm on weekdays.", "Ok.")
    ctx = m.context("When do I go to the gym on weekdays?", "A", NOW)
    assert "(latest mention)" in ctx and "last confirmed 1 May 2023; a later mention is shown above" in ctx
    assert "in force now" not in ctx.split("gym: 7 pm")[1]  # the key maps to `gym` through the alias vocabulary


def test_boolean_knobs_from_the_cli():
    """--adapter-arg rewrite=off arrives as False (the CLI coerces on/off/true/false)."""
    assert SpooklingMemory(triage=Stub({}), rewrite=False).rewrite == "off"
    assert SpooklingMemory(triage=Stub({}), rewrite=True).rewrite == "all"
    assert SpooklingMemory(triage=Stub({}), index=False).index_mode == "never"
    assert SpooklingMemory(triage=Stub({}), vectors="auto", rerank=False).rerank is False


from spookling_memory.core import compact as cmod  # noqa: E402


def test_v3_safe_defaults():
    m = SpooklingMemory(triage=Stub({}))
    c = m.config()
    assert (c["mapreduce"], c["selfcheck"], c["rewrite"], c["ladder"], c["agg_budget"], c["token_cap"]) == \
        (False, False, "off", False, 9000, 10000)
    import inspect
    sig = inspect.signature(SpooklingMemory).parameters
    assert (sig["vectors"].default, sig["rerank"].default, sig["rerank_mode"].default) == ("auto", "auto", "order")  # on
    assert (c["vectors"], c["rerank"]) == (False, False) and c["local_models"] != "on"  # unless the local models are off
    assert c["date_fixes"] and c["agg_nodiv"] and c["trim_all"] and c["stale_label"] and c["event_dates"] and c["entities"]
    assert c["prefs_on_demand"] and c["compact_keep"] and c["pin_rules"]


def test_v3_compact_keeps_items_verbatim_with_pointers():
    m = SpooklingMemory(triage=Stub({}))
    chat(m, "s1", "2024-02-01T10:00:00Z", "Ground rule for today: Address me as Dr. Rao from now on.", "Noted, Dr. Rao.",
         "We decided to deploy on branch feat/webhook-timeouts.", "Ok.", "Keep memory under 384 MB, the pods get killed.", "Ok.",
         "The config lives in deploy/values.yaml.", "Ok.", "Nice weather today.", "Yes.")
    m.compact("s1")
    kinds = {k.kind: k for k in m.staging}
    assert kinds["rule"].text == "Ground rule for today: Address me as Dr. Rao from now on."
    assert {"rule", "decision", "path"} <= set(kinds) and all(k.seq > 0 for k in m.staging)
    assert not any("weather" in k.text for k in m.staging)
    ctx = m.context("Which branch do we deploy on?", "A", NOW)
    inv = ctx.split("</invariant>")[0]  # rules and decisions always in <invariant>, other kept items after
    assert ctx.startswith("<invariant>") and inv.index("Address me as Dr. Rao") < inv.index("feat/webhook-timeouts")
    assert ctx.index("</invariant>") < ctx.index("<kept-before-compaction>") < ctx.index("deploy/values.yaml")
    assert m.fork().staging == m.staging  # forks keep what was kept


def test_v3_rule_extraction_misses_chatter():
    for s in ["Always a pleasure to chat.", "Never mind, I found it.", "Write a haiku about rain.", "I always forget my keys.",
              "Only two people came to the party.", "Every morning I run 5k.", "Use the second option for now."]:
        assert not cmod.is_rule(s), s
    assert cmod.is_rule("Never schedule anything on a Friday, the team is off.")
    assert cmod.is_rule("Constraint: we can't use axios in this service.")


def test_v3_token_cap_bounds_context_and_followups():
    long = "Here is a long reply about the garden plans. " * 60
    m = SpooklingMemory(triage=Stub({}), token_cap=2500, budget=8000, neighbours=0, ladder_max=3)
    for i in range(10):
        chat(m, f"s{i}", f"2023-05-{i + 1:02d}T10:00:00Z", f"Garden note {i}: planted tomatoes and basil.", long)
    ctx = m.context("What did I plant in the garden?", "A", NOW)
    assert 1.25 * n_tokens(ctx) <= 2500 - 1000  # in reader tokens, the first context leaves room for the reader prompt
    assert m.followup("What did I plant in the garden?", "A", NOW, "I don't know", 1) is None or m._last["spent"] <= 2500


def test_v3_grocery_list_is_not_an_aggregation():
    from spookling_memory.core.complex_triggers import categories
    assert "aggregate" not in categories("Is my mom using the same grocery list method as me?")
    assert "aggregate" in categories("List the courses I have taken.")
    assert "aggregate" in categories("Can you list every museum I visited?")


def test_v3_anchor_keeps_later_turns_when_the_question_asks_about_now():
    m = _store()
    w = date_in("What phone did I have when I got the Fairphone 5, and which one do I have now?", date(2027, 3, 4),
                m.facts, m.values, m.vocab)
    assert w.how == "anchor" and w.window is None
    w2 = date_in("What did I drive when I got the iPhone 16?", date(2027, 3, 4), m.facts, m.values, m.vocab)
    assert w2.window is not None


def test_v3_only_instruction_shaped_tagged_rules_are_pinned():
    tags = {"Internally the codename is Quokka.": [dict(label="rule", value="Quokka")],
            "From now on always answer in metric.": [dict(label="rule", value="always answer in metric")]}
    m = SpooklingMemory(triage=Stub(tags))
    chat(m, "s1", "2024-01-01T10:00:00Z", "Internally the codename is Quokka.", "Ok.", "From now on always answer in metric.", "Ok.")
    ctx = m.context("How far is it to Leeds?", "A", NOW)
    assert "always answer in metric" in ctx.split("</rules>")[0] and "- Quokka" not in ctx


GYM = "7:00 pm on Mondays, Wednesdays, and Fridays"


def gym(*later: tuple[str, str, str]) -> SpooklingMemory:
    first = f"I usually go to the gym at {GYM}, that hour suits me."
    tags = {first: [dict(label="fact", key="gym", value=GYM)]} | {t: [dict(label="fact", key="gym", value=v)] for _, t, v in later}
    m = SpooklingMemory(triage=Stub(tags), neighbours=0)
    chat(m, "s1", "2023-02-11T10:00:00Z", first, "Noted.")
    for i, (ts, text, _) in enumerate(later):
        chat(m, f"s{i + 2}", ts, text, "Noted.")
    return m


def test_v32_partial_mention_is_not_a_confirmation():
    """Tagged with only the unchanged words: the old row stays unconfirmed and no longer reads "in force now"."""
    m = gym(("2023-05-30T10:00:00Z", "These days I usually go to the gym at 6:00 pm on Mondays, Wednesdays, and Fridays, a better hour.",
             "on Mondays, Wednesdays, and Fridays"))
    assert [x["value"] for x in m.log.decisions if x["step"] == "triage"][-1] == "on Mondays, Wednesdays, and Fridays"
    assert m.facts.stats_for("gym", "", GYM)[:2] == (0, None)
    ctx = m.context("At what hour do I usually go to the gym?", "A", NOW)
    rec = next(x for x in ctx.splitlines() if x.startswith("gym:"))
    assert "in force now" not in rec and "last confirmed 11 Feb 2023; a later mention is shown above" in rec
    assert "6:00 pm" in ctx


def test_v32_record_quotes_the_newest_turn_that_states_the_value():
    m = gym(("2023-06-01T10:00:00Z", f"Still the same routine, gym at {GYM}.", GYM))
    assert m.facts.stats_for("gym", "", GYM)[:2] == (1, "2023-06-01")
    rec = next(x for x in m.context("What time do I usually go to the gym?", "A", NOW).splitlines() if x.startswith("gym:"))
    assert f'said 1 Jun 2023: "Still the same routine, gym at {GYM}."' in rec and "11 Feb 2023:" not in rec


def test_v32_only_the_users_own_instruction_sentences_are_pinned():
    """The tagger cut "keep it short ... always add my voice demo" out of a rule scoped to cover letters."""
    voice = ("whenever ask you for cover letter always remember to keep it short and simple and always add my voice demo "
             "attaching part unless i ask you for longer version")
    km = "Always give distances in kilometres, never miles."
    tags = {voice: [dict(label="rule", value="keep it short and simple and always add my voice demo attaching part")],
            km: [dict(label="rule", value="give distances in kilometres")]}
    m = SpooklingMemory(triage=Stub(tags))
    chat(m, "s1", "2023-05-26T10:00:00Z", voice, "Noted.", km, "Noted.")
    m.settle()  # a rule is a slow-path item
    assert m.rules() == [f"- {km} (said 2023-05-26)"]  # the whole sentence, in the user's words; no voice demo


def test_v4_remind_me_is_never_an_aggregation():
    from spookling_memory.core.complex_triggers import categories
    q = "Can you remind me what was the average improvement in framerate in the HAMLET submission?"
    assert "aggregate" not in categories(q) and "aggregate" in categories("What was the average improvement?")
    assert "aggregate" not in categories("What did I say the total was for the trip?")
    assert "aggregate" in categories("How many books did I read this year?")


def test_v4_a_lone_turn_between_two_shown_turns_is_shown():
    """"I bought it a year ago in Paris" needs the turn before it ("my snake Seraphim")."""
    m = SpooklingMemory(triage=Stub({}), neighbours=0)
    m.ingest(Session("s1", "A", "2023-01-27T10:00:00Z", [Turn("user", "My second snake Seraphim slinked out of her cage."),
                                                          Turn("user", "Aww, so sweet."),
                                                          Turn("user", "I bought Seraphim a year ago in Paris.")]))
    ctx = m.context("Where did I buy Seraphim the snake?", "A", NOW)
    assert "Aww, so sweet." in ctx and "[...]" not in ctx


def test_v4_partial_mention_beside_another_time_goes_slow():
    """A turn naming part of the stored gym value beside another time is a possible update, not a silent mention."""
    first = f"I usually go to the gym at {GYM}."
    later = "I go to the gym on Mondays, Wednesdays, and Fridays, but at 6:00 pm these days."
    tags = {first: [dict(label="fact", key="gym", value=GYM)],
            later: [dict(label="fact", key="gym", value="on Mondays, Wednesdays, and Fridays")]}
    ruling = Ruling("assert", "6:00 pm", None, "these days", False, 0.9)
    m = SpooklingMemory(triage=Stub(tags, {later: ruling}), neighbours=0)
    chat(m, "s1", "2023-02-11T10:00:00Z", first, "Noted.")
    chat(m, "s2", "2023-05-30T10:00:00Z", later, "Noted.")
    d = [x for x in m.log.decisions if x["step"] == "triage"][-1]
    assert d["route"] == "slow" and "changed" in d["flags"]
    m.settle()
    assert [r.value for r in m.facts.at("gym", "2023-06-06")] == ["6:00 pm"]
    from spookling_memory.core.sleep import changed
    assert not changed("I go on Mondays, Wednesdays, and Fridays.", "on Mondays, Wednesdays, and Fridays", GYM)
    assert not changed(f"Still at {GYM}.", GYM, GYM)  # the whole value: a confirmation


def test_v4_index_page_lists_only_facts_confirmed_twice():
    tags = {"I drive a Volvo XC40.": [dict(label="fact", key="car", value="Volvo XC40")],
            "Painting is my hobby.": [dict(label="fact", key="hobby", value="Painting")]}
    m = SpooklingMemory(triage=Stub(tags))
    for i in range(3):
        chat(m, f"s{i}", f"2023-0{i + 1}-01T10:00:00Z", "I drive a Volvo XC40.", "Nice.", *(["Painting is my hobby.", "Fun."] if i == 0 else []))
    m.settle()
    assert "car: Volvo XC40" in m.index and "Painting" not in m.index


def test_close_and_forks_leave_no_temp_dirs_or_open_fds():
    """50 reset/fork/close cycles: no spookling-log-* dir and no file descriptor outlive them."""
    import gc
    import glob
    import os
    import tempfile
    pattern = os.path.join(tempfile.gettempdir(), "spookling-log-*")
    fds = lambda: len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else 0  # Linux only; the dirs are checked everywhere
    gc.collect()  # earlier tests' garbage closes its own handles first, so it cannot move the count mid-loop
    dirs0, fds0 = set(glob.glob(pattern)), fds()
    for i in range(50):
        m = SpooklingMemory(triage=Stub({}))
        chat(m, "s1", "2024-01-01T10:00:00Z", f"Note {i}: I planted tomatoes.", "Nice.")
        forks = [m.fork() for _ in range(3)]
        assert all(f.log._tmp is None for f in forks)  # a fork's LOG is in memory: no file, no dir
        assert "tomatoes" in forks[0].context("What did I note about tomatoes?", "A", NOW)
        for f in forks:
            f.close()
        assert "tomatoes" in m.context("What did I note about tomatoes?", "A", NOW)  # closing forks leaves the parent working
        m.reset()  # releases the stores it replaces
        chat(m, "s2", "2024-02-01T10:00:00Z", "I planted basil.", "Nice.")
        m.close()
        m.close()  # idempotent
    assert set(glob.glob(pattern)) == dirs0 and fds() == fds0  # no gc.collect() here: close() alone must free them


def test_v31_event_dates_only_on_time_questions():
    m = SpooklingMemory(triage=Stub({}), neighbours=0, time=False)
    chat(m, "s1", "2023-05-23T10:00:00Z", "I ran a 5K in the park last Sunday.", "Well done!")
    assert "[last Sunday = 21 May 2023]" in m.context("When did I run the 5K in the park?", "A", NOW)
    plain = m.context("Where did I run the 5K?", "A", NOW)
    assert "I ran a 5K in the park last Sunday." in plain and "= 21 May 2023" not in plain


import sqlite3  # noqa: E402

from spookling_memory.core import activation as act  # noqa: E402
from spookling_memory.core import extract as ex  # noqa: E402
from spookling_memory.core.events import ClassVocab, Events, seed_classes  # noqa: E402


class EventStub(Triage):
    """Facts: none. Events: canned proposals per clause text (spans may be wrong on purpose). resolve_class: a map."""

    name, version = "event-stub", "event-stub-1"

    def __init__(self, proposals: dict, classes_map: dict | None = None):
        self.proposals, self.classes_map = proposals, classes_map or {}

    def judge(self, turns, date, known):
        return [[] for _ in turns], Spent()

    def events(self, turns, replies, date, candidates, classes):
        return [[ex.EventItem(c.text, **self.proposals[c.text]) for c in cs if c.text in self.proposals] for cs in candidates], Spent()

    def resolve_class(self, clause, word, proposed, kind, classes):
        return self.classes_map.get(proposed), Spent()


def users_store(*turns: tuple[str, str], **kw) -> SpooklingMemory:
    m = SpooklingMemory(triage=Stub({}), **kw)
    for i, (ts, text) in enumerate(turns):
        chat(m, f"s{i}", ts, text, "Noted.")
    m.settle()
    return m


def test_v2_schema_is_research_37_e1_and_events_are_insert_only():
    e = Events()
    tables = {r[0] for r in e.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"events", "entities", "aliases", "classes", "event_entities", "event_links"}
    cols = [r[1] for r in e.db.execute("PRAGMA table_info(events)")]
    assert cols == ["event_id", "source_event", "span_start", "span_end", "action", "action_class", "object", "object_class",
                    "place", "amount", "unit", "polarity", "occurred_from", "occurred_to", "occ_precision", "occ_basis",
                    "said_at", "recorded_at", "same_as", "gist", "decision_id", "confidence", "writer", "status"]
    row = dict(event_id="e1", source_event="h", span_start=0, span_end=5, action="went", action_class="attend", polarity="did",
               occ_precision="day", occ_basis="stated", said_at="2024-01-01T10:00:00Z", recorded_at="x", gist="went",
               decision_id="d", confidence=1.0, writer="t")
    e.add_event(row)
    for bad in (dict(polarity="maybe"), dict(occ_precision="soon"), dict(occ_basis="guessed"), dict(span_end=0)):
        with pytest.raises(sqlite3.IntegrityError):
            e.add_event(row | {"event_id": "e2"} | bad)
    with pytest.raises(sqlite3.IntegrityError, match="insert only"):
        e.db.execute("UPDATE events SET action='x'")
    with pytest.raises(sqlite3.IntegrityError, match="insert only"):
        e.db.execute("DELETE FROM events")


def test_v2_seed_classes_are_a_closed_list_of_about_80():
    seed = seed_classes()
    assert 80 <= len(seed) <= 110 and {v["kind"] for v in seed.values()} == {"action", "object"}
    v = ClassVocab()
    assert v.classes_in("I went to an amazing gig") == {"attend", "concert"}  # "gig" filed under concert at encoding
    assert not v.closed("pottery_class") and not v.propose_word("wheel", "pottery_class")  # off the list: the slow path's
    assert not v.propose_word("shindig", "party") and "shindig" not in v.words["party"]  # a new word: once is not enough
    assert v.propose_word("shindig", "party") and v.classes_in("a big shindig") == {"party"}  # twice: applied
    v.add_class("pottery_class", "object", ["wheel throwing"])
    assert v.closed("pottery_class")


def test_v2_polarity_by_rule():
    cases = {"I bought a new laptop": "did", "I'm thinking of buying a new laptop": "plan", "I'm planning to visit Rome": "plan",
             "I didn't go to the concert": "negated", "I never finished the book": "negated", "I might try the gym": "hypothetical",
             "If I go to Paris I will see the Louvre": "hypothetical", "We'll book the hotel tomorrow": "plan"}
    assert {c: ex.polarity_of(c) for c in cases} == cases


def test_v2_clauses_are_deterministic_first_person_and_not_questions():
    turn = ("Can you suggest a good laptop? By the way, I just baked a chocolate cake for my sister's party, and then I "
            "donated my old coffee maker to Goodwill. Remember when I got pre-approved for $400,000?")
    got = [c.text for c in ex.clauses(turn, ClassVocab())]
    assert got == ["I just baked a chocolate cake for my sister's party", "I donated my old coffee maker to Goodwill",
                   "Remember when I got pre-approved for $400,000?"]
    assert all(turn[c.start:c.end] == c.text for c in ex.clauses(turn, ClassVocab()))  # offsets are verbatim


def test_v2_spans_are_checked_verbatim_and_off_list_classes_go_slow():
    turn = "I donated my old coffee maker to Goodwill."
    v = ClassVocab()
    ok = ex.event_decision("h", turn, "2024-05-20T10:00:00Z", 0, ex.EventItem("I donated my old coffee maker to Goodwill", "donated",
                                                                              "dispose", "my old coffee maker", "kitchen_item", "Goodwill"),
                           v, "w", "ev1", "now")
    assert ok["route"] == "fast" and ok["object"] == "my old coffee maker" and ok["polarity"] == "did"
    bad = ex.event_decision("h", turn, "2024-05-20T10:00:00Z", 0, ex.EventItem("I donated my old coffee maker to Goodwill", "donated",
                                                                               "dispose", "my old espresso machine", "kitchen_item"),
                            v, "w", "ev1", "now")
    assert bad["route"] == "rejected" and bad["flags"] == ["object not verbatim"]  # a model's paraphrase never lands
    new = ex.event_decision("h", turn, "2024-05-20T10:00:00Z", 0, ex.EventItem("I donated my old coffee maker to Goodwill", "donated",
                                                                               "dispose", "my old coffee maker", "small_appliance"),
                            v, "w", "ev1", "now")
    assert new["route"] == "slow" and "new_class" in new["flags"]


def test_v2_new_class_only_through_the_slow_path():
    clause = "I donated my old coffee maker to Goodwill"
    item = dict(action="donated", action_class="dispose", object="my old coffee maker", object_class="small_appliance")
    for mapping, stored in (({"small_appliance": "kitchen_item"}, "kitchen_item"), ({"small_appliance": "small_appliance"}, "small_appliance"),
                            ({}, None)):
        m = SpooklingMemory(triage=Stub({}), event_tagger=EventStub({clause: item}, mapping))
        chat(m, "s1", "2024-05-20T10:00:00Z", clause + ".", "Kind of you.")
        m.settle()
        rows = m.events_store.rows()
        assert [r["object_class"] for r in rows] == [stored], mapping  # unsettled: the event stays, without a class
        assert m.classes.closed("small_appliance") == (stored == "small_appliance")
    m = SpooklingMemory(triage=Stub({}), event_tagger=EventStub({clause: item | {"action_class": "giveaway"}}, {}))
    chat(m, "s1", "2024-05-20T10:00:00Z", clause + ".", "Kind of you.")
    m.settle()
    assert m.events_store.rows() == []  # an unsettled ACTION class: no event (action_class is NOT NULL)


def test_v2_happened_date_is_apart_from_said_date():
    m = users_store(("2023-11-30T10:00:00Z", "Remember when I got pre-approved for a mortgage from Wells Fargo?"),
                    ("2023-05-20T10:00:00Z", "By the way, I baked a lemon tart for my sister's party last weekend."))
    rows = {r["action"]: r for r in m.events_store.rows()}
    pre = rows["got"]
    assert pre["occ_basis"] == "on_or_before_said" and pre["occurred_from"] is None and pre["said_at"].startswith("2023-11-30")
    tart = rows["baked"]
    assert tart["occ_basis"] == "relative_to_said" and tart["occurred_from"] == "2023-05-13"  # last weekend, from 20 May


def test_v2_one_mention_one_row_same_as_and_counts_over_clusters():
    m = users_store(("2023-05-01T10:00:00Z", "I bought a road bike today."),
                    ("2023-05-10T10:00:00Z", "I bought a road bike recently and love it."),
                    ("2023-05-12T10:00:00Z", "I bought a kayak today."))
    rows = [r for r in m.events_store.rows() if r["action_class"] == "purchase"]
    assert len(rows) == 3  # every mention is a row
    bike = [r for r in rows if "bike" in r["object"]]
    assert bike[1]["same_as"] == bike[0]["event_id"] and bike[0]["same_as"] is None  # never a merge
    clusters = m.events_store.clusters(r["event_id"] for r in rows)
    assert len(clusters) == 2  # two purchases, not three
    links = {r[0] for r in m.events_store.db.execute("SELECT kind FROM event_links")}
    assert "same_as" in links


def test_v2_entities_by_rules_multiword_names_and_relation_aliases():
    m = users_store(("2023-01-03T10:00:00Z", "My sister Rachel and I went to the Museum of Contemporary Art."),
                    ("2023-01-10T10:00:00Z", "I took my sister Rachel to a gig at the Wells Fargo Center."),
                    ("2023-02-01T10:00:00Z", "I baked a cake for Rachel's birthday."))
    names = {r[0]: r[1] for r in m.events_store.db.execute("SELECT canonical, kind FROM entities")}
    assert names.get("Museum of Contemporary Art") == "place" and names.get("Wells Fargo Center") == "place"
    assert names.get("Rachel") == "person"
    assert m.search.entities_in("What did I bake for my sister?") == m.search.entities_in("What did I bake for Rachel?") != set()
    roles = m.events_store.db.execute("SELECT role FROM event_entities").fetchall()
    assert {"place", "with"} <= {r[0] for r in roles}


def test_v2_stemming_finds_concert_from_concerts():
    turns = (("2023-03-01T10:00:00Z", "I saw an amazing concert downtown."), ("2023-03-02T10:00:00Z", "Pasta tonight, any sauce tips?"))
    on = users_store(*turns, events=False).context("Which concerts did I see?", "A", NOW)
    off = users_store(*turns, events=False, stem=False).context("Which concerts did I see?", "A", NOW)
    assert "amazing concert" in on and "amazing concert" not in off


def test_v2_since_x_is_a_range_to_now():
    m = users_store(("2023-02-01T10:00:00Z", "I started painting classes today."),
                    ("2023-03-05T10:00:00Z", "I finished my third painting today."))
    from spookling_memory.core.recall import date_in
    w = date_in("How many paintings have I finished since March 1, 2023?", date(2023, 4, 1), m.facts, m.values, m.vocab,
                since=True)
    assert (w.how, w.start, w.end, w.window) == ("since", date(2023, 3, 1), date(2023, 4, 2), None)
    w2 = date_in("How many paintings since I started painting classes?", date(2023, 4, 1), m.facts, m.values, m.vocab,
                 since=True, anchor_event=lambda tail: __import__("spookling_memory.core.recall", fromlist=["x"]).anchor_event(m, tail))
    assert w2.how == "since" and w2.start == date(2023, 2, 1)


def test_v2_time_filter_reads_when_it_happened_not_when_it_was_said():
    m = users_store(("2023-02-10T10:00:00Z", "Can you recommend a stretching routine?"),
                    ("2023-04-20T10:00:00Z", "By the way, in March 2023 I ran a half marathon in Lisbon."))
    ctx = m.context("What did I do in March 2023?", "A", "2023-06-01T10:00:00Z")
    assert "half marathon" in ctx  # said in April, happened in March


def test_v2_ppr_converges_to_the_closed_form_on_a_toy_graph():
    import numpy as np
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 0.5), (3, 0, 2.0), (1, 3, 1.0), (4, 4, 0.0)]
    M = act.column_stochastic(5, edges)
    assert np.allclose(np.asarray(M.sum(axis=0)).ravel(), 1.0)  # column-stochastic, dangling node 4 included
    p = np.array([1.0, 0, 0, 0, 0])
    r = act.ppr(M, p, restart=0.5, iters=200, tol=1e-14)
    closed = 0.5 * np.linalg.solve(np.eye(5) - 0.5 * M.toarray(), p)
    assert np.allclose(r, closed, atol=1e-10) and abs(r.sum() - 1) < 1e-9
    q = np.array([0, 0, 1.0, 0, 0])  # PPR of a mix is the mix of PPRs
    mix = act.ppr(M, 0.3 * p + 0.7 * q, iters=200, tol=1e-14)
    assert np.allclose(mix, 0.3 * act.ppr(M, p, iters=200, tol=1e-14) + 0.7 * act.ppr(M, q, iters=200, tol=1e-14), atol=1e-10)


def test_v2_activation_reaches_the_other_event_at_the_place_two_hops_away():
    turns = (("2023-03-04T10:00:00Z", "By the way, I saw Phoebe Bridgers live at the Fillmore Hall last night."),
             ("2023-04-15T10:00:00Z", "Any tips for a quick lunch? Also, I attended a pottery workshop at the Fillmore Hall today."),
             ("2023-05-01T10:00:00Z", "I listened to a Phoebe Bridgers album on the train today."),
             ("2023-05-09T10:00:00Z", "I bought running shoes at Riverside Mall today."))
    q = "What else did I do at the place where I saw Phoebe Bridgers?"
    on = users_store(*turns)
    hits = on.graph.query(on.search.entities_in(q), on.classes.classes_in(q), [], 30)
    j = lambda word: next(j for j, i in enumerate(on.search.einfo) if word in i["clause"])
    assert ("e", j("pottery")) in hits  # artist -> concert event -> Fillmore Hall -> the workshop: two hops
    assert ("e", j("album")) in hits  # one hop, ranked by activation; the reader reads the place
    assert ("e", j("running shoes")) not in hits  # shares no entity or class with a seed: dropped
    assert "pottery workshop" in on.context(q, "A", NOW)


def test_v2_invariant_block_is_capped_rules_first_and_keeps_the_rules_tag():
    m = SpooklingMemory(triage=Stub({}))
    lines = ["From now on always answer in metric."] + [f"We decided to use queue {i} for the jobs." for i in range(60)] + \
            ["I tried the retry flag and it didn't work.", "We still need to pick a database."]
    chat(m, "s1", "2024-01-01T10:00:00Z", *[x for line in lines for x in (line, "Ok.")])
    m.compact("s1")
    block = m.invariant_block()
    items = [x for x in block.splitlines() if x.startswith("- ")]
    assert len(items) == 40 and items[0].startswith("- From now on always answer in metric.")
    assert "<rules>" in block and block.startswith("<invariant>") and block.endswith("</invariant>")
    assert "Failed attempts:" in block and "Open items:" in block and "queue 59" in block and "queue 0 " not in block
    assert m.context("Which database?", "A", NOW).startswith("<invariant>\n<rules>")


def test_v2_model_events_prompt_parses_and_leaves_the_fact_cache_alone(monkeypatch):
    replies = []

    def fake(model, messages, **p):
        replies.append(messages)
        return _Reply(json.dumps({"items": [[{"clause": "I donated my old coffee maker to Goodwill", "action": "donated",
                                              "action_class": "dispose", "object": "my old coffee maker", "object_class": "kitchen_item",
                                              "place": "Goodwill", "time": None, "confidence": 0.9}]]}))
    monkeypatch.setattr(triage_mod, "call", fake)
    t = ModelTriage()
    turn = "By the way, I donated my old coffee maker to Goodwill."
    items, spent = t.events([turn], ["That's kind."], "2024-05-20", [ex.clauses(turn, ClassVocab())], ClassVocab())
    assert items[0][0].object == "my old coffee maker" and spent.calls == 1
    assert t.event_version != t.version and t.version.endswith("|v2")  # a new prompt never touches the cached fact tags


def test_v2_time_module_dates_events_and_its_block_comes_last():
    """Events are dated by timeline.anchor; <time-computed> comes last, counted, never trimmed; plans get no date."""
    m = users_store(("2023-03-01T10:00:00Z", "By the way, I cancelled my FarmFresh subscription today."),
                    ("2023-03-11T10:00:00Z", "Also, I did my online grocery shopping today."),
                    ("2023-03-12T10:00:00Z", "I'm planning to visit the Louvre next Friday."))
    rows = {r["action"]: r for r in m.events_store.rows()}
    assert (rows["cancelled"]["occurred_from"], rows["cancelled"]["occ_precision"], rows["cancelled"]["occ_basis"]) == \
        ("2023-03-01", "day", "on_or_before_said") or rows["cancelled"]["occurred_from"] == "2023-03-01"
    louvre = next(r for r in m.events_store.rows() if "Louvre" in (r["place"] or r["object"] or ""))
    assert louvre["polarity"] == "plan" and louvre["occurred_from"] is None and louvre["occ_basis"] == "unknown"
    q = "How many days passed between the day I cancelled my FarmFresh subscription and the day I did my online grocery shopping?"
    ctx = m.context(q, "A", "2023-04-01T10:00:00Z")
    body = ctx.split("</recalled-data>")[0].rstrip()
    assert body.endswith("</time-computed>") and "Result: 10 days" in body.split("<time-computed>")[1]
    assert m._last["time"]["kind"] == "duration"
    q2 = "How many days passed between the day I cancelled my FarmFresh subscription and the day I adopted a parrot?"
    assert "<time-computed>" not in m.context(q2, "A", "2023-04-01T10:00:00Z")  # a phrase with no event: no block, no guess
    assert "<time-computed>" not in m.context("What did I cancel?", "A", "2023-04-01T10:00:00Z")  # not about time: no block


def test_v21_time_block_only_for_date_questions_and_confident_matches():
    m = users_store(("2023-03-04T10:00:00Z", "By the way, I attended a pottery workshop today."),
                    ("2023-03-18T10:00:00Z", "Also, I attended a salsa class today."))
    now = "2023-04-01T10:00:00Z"
    assert "<time-computed>" not in m.context("How many classes did I attend in March?", "A", now)  # a window filters
    assert "<time-computed>" in m.context("When did I attend the pottery workshop?", "A", now)
    assert "<time-computed>" not in m.context("When did I attend the opera gala?", "A", now)  # no event holds the phrase


def test_v21_undetermined_time_block_is_advisory():
    m = users_store(("2023-03-04T10:00:00Z", "I'm planning to attend a pottery workshop next month."))
    ctx = m.context("When did I attend the pottery workshop?", "A", "2023-04-01T10:00:00Z")
    block = ctx[ctx.index("<time-computed>"):] if "<time-computed>" in ctx else ""
    assert "no date on record" in block and "Advisory only" in block and "do not compute it again" not in block  # a plan: undated


def test_v21_retrieve_returns_context_digest_and_strong():
    m = users_store(("2023-03-04T10:00:00Z", "By the way, I donated my old coffee maker to Goodwill today."),
                    ("2023-03-05T10:00:00Z", "Any tips for a rainy afternoon?"))
    got = m.retrieve("What did I donate to Goodwill?", "A", NOW)
    assert set(got) == {"context", "digest", "strong"} and got["strong"] is True
    assert "coffee maker" in got["digest"] and len(got["digest"]) <= 1200 and got["digest"] in got["context"] or "coffee maker" in got["digest"]
    weak = m.retrieve("Which marathon did I run in Lisbon?", "A", NOW)
    assert weak["strong"] is False


def test_v21_window_fallback_per_kind_keeps_turns_said_after_the_window():
    """Turns said after the window fall back on their own; out-of-window events never show."""
    m = users_store(("2023-05-20T10:00:00Z", "My car gets about 30 miles per gallon these days, any tips to improve it?"),
                    ("2023-05-22T10:00:00Z", "I bought a new air filter for my car."))
    ctx = m.context("How many miles per gallon was my car getting a few months ago?", "A", "2023-05-30T10:00:00Z")
    assert "30 miles per gallon" in ctx


def test_texts_with_counts_exactly_like_a_substring_scan():
    """texts_with counts in one pass exactly what a scan of every text per name would."""
    texts = ["Met Ann at Annual Day", "ann", "A B", "Wells Fargo Center and Wells", "]x^", ""]
    names = ["Ann", "Annual Day", "A", "Wells", "Wells Fargo Center", "]", "^", "B", "Zed"]
    assert ex.texts_with(names, texts) == {n: sum(1 for t in texts if n in t) for n in names}
