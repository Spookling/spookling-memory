"""Offline fixtures: a stub Triage stands in for the writer model (as memory-bench's own tests do), local models are
off, and any model call fails the test."""
import os

os.environ["SPOOKLING_LOCAL_MODELS"] = "0"  # no embedder or reranker

import pytest  # noqa: E402
from bench.adapters.spookling import triage as triage_mod  # noqa: E402
from bench.adapters.spookling.triage import Item, Ruling, Spent, Triage  # noqa: E402

from spookling_memory.store import Store  # noqa: E402


class Stub(Triage):
    """Canned tags per user turn; resolve() accepts the tagger's proposal."""

    name, version = "stub", "stub-1"

    def __init__(self, tags: dict):
        self.tags, self.judged = tags, []

    def judge(self, turns, date, known):
        self.judged.append(list(turns))
        return [[Item(**x) for x in self.tags.get(t, [])] for t in turns], Spent(calls=1, tokens=10)

    def resolve(self, turn, date, proposed, history, flags):
        return Ruling(proposed["op"], proposed["value"], proposed["old"], proposed["when"]), Spent(calls=1, tokens=5)


TAGS = {
    "I drive a Toyota Corolla, nothing fancy.": [dict(label="fact", key="car", value="Toyota Corolla")],
    "This Pixel 8 camera is honestly great.": [dict(label="fact", key="phone", value="Pixel 8")],
    "From now on, always answer in metric units.": [dict(label="rule", value="always answer in metric units")],
    "I drive a Honda Civic now.": [dict(label="fact", key="car", value="Honda Civic")],
}
HISTORY = [  # (session id, ts, user turns); each user turn gets the reply "Noted."
    ("s1", "2024-05-22T10:00:00Z", ["I drive a Toyota Corolla, nothing fancy.", "How do I roast peppers?"]),
    ("s2", "2024-09-23T19:35:00Z", ["This Pixel 8 camera is honestly great.", "We decided to use Postgres 16 for the shop."]),
    ("s3", "2024-11-02T09:00:00Z", ["From now on, always answer in metric units.", "I went to a jazz concert at the Blue Note."]),
]
RESET_AT = "2025-01-01T00:00:00Z"
AFTER = ("s4", "2025-02-01T10:00:00Z", ["I drive a Honda Civic now."])


def turns(users):
    return [t for u in users for t in ({"role": "user", "content": u}, {"role": "assistant", "content": "Noted."})]


def fill(store, sessions=HISTORY):
    for sid, ts, users in sessions:
        store.ingest(sid, turns(users), ts)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test made a model call")

    monkeypatch.setattr(triage_mod, "call", refuse)


@pytest.fixture
def open_store(tmp_path):
    """open_store(name="m") -> a Store on tmp_path/name with the stub triage; every one is closed after the test."""
    made = []

    def make(name="m", tags=TAGS):
        s = Store(tmp_path / name, triage=Stub(tags))
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


NOW = "2025-03-01T10:00:00Z"
QUESTIONS = ["What car do I drive?", "What phone do I have?", "What car did I drive in June 2024?",
             "What did we decide for the shop database?", "When did I go to the jazz concert?"]


def believed(store):
    """(attribute, value, valid_from, valid_to) of every row believed now."""
    return [(r.attribute, r.value, r.valid_from, r.valid_to) for r in store.mem.facts._rows(
        "SELECT * FROM facts WHERE superseded_at='9999-12-31' ORDER BY attribute, valid_from")]


def snapshot(store):
    """Every view the full sleep rebuilds, plus the side state replayed from the judgements."""
    m = store.mem
    return {"facts": m.facts.all_rows(), "events": m.events_store.rows() if m.events_store else [],
            "staging": list(m.staging), "rules": list(m.facts.rules), "vocab": m.vocab.words, "index": m.index,
            "classes": (m.classes.kind, m.classes.words, m.classes.pending)}
