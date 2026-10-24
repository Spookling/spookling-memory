"""Reset: facts closed at T, earlier turns hidden unless asked about, rules unpinned, nothing deleted, undo restores."""
import pytest
from conftest import AFTER, NOW, QUESTIONS, RESET_AT, believed, fill, turns

from spookling_memory import reset as R
from spookling_memory.core import sleep
from spookling_memory.core.recall import ABSTAIN

CLOSED = [("car", "Toyota Corolla", "2024-05-22", "2025-01-01"), ("phone", "Pixel 8", "2024-09-23", "2025-01-01")]


@pytest.fixture
def s(open_store):
    store = open_store()
    fill(store)
    store.compact("s2")
    store.compact("s3")
    store.retrieve(QUESTIONS[0], NOW)  # settled: the slow path has judged what was pending
    return store


def test_reset_is_one_ruling_and_closes_every_open_fact_at_T(s, tmp_path):
    rows, n_events, n_dec = s.mem.facts.all_rows(), len(s.mem.log.events), len(s.mem.log.decisions)
    r = s.reset("fresh start", RESET_AT)
    assert (r["step"], r["at"], r["note"], r["upto"]) == ("reset", RESET_AT, "fresh start", n_events)
    assert len(s.mem.log.decisions) == n_dec + 1 and s.mem.log.decisions[-1] == r
    assert len(s.mem.log.events) == n_events
    assert believed(s) == CLOSED
    key = lambda x: (x["attribute"], x["value"], x["valid_from"], x["valid_to"])
    assert {key(x) for x in rows} <= {key(x) for x in s.mem.facts.all_rows()}  # what was believed is still there
    assert (tmp_path / "m" / "judgements.jsonl").read_text().splitlines()[-1] == s.mem.log.lines()[1][-1]


def test_earlier_turns_and_events_show_only_for_questions_dated_before_T(s):
    concerts = [x for x in s.mem.events_store.rows() if "concert" in (x["object"] or "")]
    assert concerts and "jazz concert" in s.retrieve("When did I go to the jazz concert?", NOW)["context"]
    s.reset("", RESET_AT)
    assert R.before_reset(s.mem) == {e.hash for e in s.mem.log.events}
    for q in ("What car do I drive?", "What did we decide for the shop database?", "When did I go to the jazz concert?"):
        assert s.retrieve(q, NOW)["context"] == ABSTAIN
    dated = s.retrieve("What car did I drive in June 2024?", NOW)["context"]
    assert "Toyota Corolla" in dated and "to 1 Jan 2025" in dated
    assert "jazz concert" in s.retrieve("Did I go to a concert in November 2024?", NOW)["context"]
    fill(s, [AFTER])
    now = s.retrieve("What car do I drive?", NOW)["context"]
    assert "Honda Civic" in now and "Corolla" not in now
    view = s._view(R.latest(s.mem.log.decisions))
    assert {e.hash for e in view.log.events}.isdisjoint(R.before_reset(s.mem))
    assert all(x["source_event"] not in R.before_reset(s.mem) for x in view.events_store.rows())
    assert s.stats()["before_reset"] == 12 and s.stats()["reset"]["at"] == RESET_AT


def test_rules_and_keepers_from_before_T_are_not_pinned_but_kept(s):
    assert "metric" in s.retrieve("What car do I drive?", NOW)["context"]
    assert "Postgres 16" in s.compact("s2")["block"]
    s.reset("", RESET_AT)
    assert "metric" not in s.retrieve("What car did I drive in June 2024?", NOW)["context"]  # the whole-memory answer
    fill(s, [AFTER])
    assert "metric" not in s.retrieve("What car do I drive?", NOW)["context"]  # the after-reset view
    assert s.compact("s2")["block"] == ""
    assert {k.kind for k in s.mem.staging} >= {"rule", "decision"} and s.mem.facts.rules  # still in the data
    s.ingest("s5", turns(["From now on, always answer in metric units."]), "2025-02-02T10:00:00Z")
    s.compact("s5")
    assert "metric" in s.retrieve("What car do I drive?", NOW)["context"]  # said again after the reset: pinned


def test_undo_restores_every_view(s):
    facts, before = s.mem.facts.view_lines(), [s.retrieve(q, NOW) for q in QUESTIONS]
    r = s.reset("", RESET_AT)
    u = s.undo_reset()
    assert (u["step"], u["of"]) == ("reset_undo", r["id"]) and R.latest(s.mem.log.decisions) is None
    assert s.mem.facts.view_lines() == facts
    assert [s.retrieve(q, NOW) for q in QUESTIONS] == before
    with pytest.raises(ValueError, match="no reset"):
        s.undo_reset()


def test_a_full_tidy_and_a_restart_keep_the_reset(s, open_store):
    s.reset("", RESET_AT)
    fill(s, [AFTER])
    s.retrieve("What car do I drive?", NOW)  # settles the session after the reset
    want = sorted(CLOSED + [("car", "Honda Civic", "2025-02-01", "9999-12-31")])
    assert sorted(believed(s)) == want
    sleep.full(s.mem)  # the full tidy: FACTS folded again from zero
    assert sorted(believed(s)) == want
    s.close()
    t = open_store()
    assert sorted(believed(t)) == want
    assert "Corolla" not in t.retrieve("What car do I drive?", NOW)["context"]


def test_a_fold_without_a_reset_is_the_adapters_own(s):
    decisions = s.mem.log.decisions
    assert R.fold(decisions).all_rows() == R._fold(decisions).all_rows()


def test_the_reset_cannot_predate_the_log_nor_a_session_the_reset(s):
    n = len(s.mem.log.decisions)
    with pytest.raises(ValueError, match="predate"):
        s.reset("", "2024-10-01T00:00:00Z")
    assert len(s.mem.log.decisions) == n
    s.reset("", RESET_AT)
    with pytest.raises(ValueError, match="predates the reset"):
        s.ingest("late", turns(["I drive a Honda Civic now."]), "2024-12-31T23:00:00Z")
    assert len(s.mem.log.events) == 12


def test_judgements_pending_at_the_reset_fall_before_its_ruling(open_store):
    store = open_store()
    fill(store)  # not settled: the rule tag still waits for the slow path
    r = store.reset("", RESET_AT)
    steps = [d["step"] for d in store.mem.log.decisions]
    assert store.mem.log.decisions[-1] == r and steps.index("resolve") < steps.index("reset")


def test_two_resets_and_undoing_the_second(s):
    s.reset("first", RESET_AT)
    fill(s, [AFTER])
    second = s.reset("second", "2025-02-15T00:00:00Z")
    assert s.retrieve("What car do I drive?", NOW)["context"] == ABSTAIN
    assert sorted(believed(s)) == sorted(CLOSED + [("car", "Honda Civic", "2025-02-01", "2025-02-15")])
    assert s.undo_reset()["of"] == second["id"]
    assert "Honda Civic" in s.retrieve("What car do I drive?", NOW)["context"]  # the first reset still holds
    assert "Corolla" not in s.retrieve("What car do I drive?", NOW)["context"]


def test_a_reset_on_an_empty_memory(open_store):
    store = open_store()
    r = store.reset("", RESET_AT)
    assert (r["upto"], r["source"]) == (0, "")
    assert store.retrieve("What car do I drive?", NOW)["context"] == ABSTAIN
    fill(store, [AFTER])
    assert "Honda Civic" in store.retrieve("What car do I drive?", NOW)["context"]
