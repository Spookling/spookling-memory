"""The Jev decider, offline: a stub Decisions API answers every typed question; one test per gate."""
import os

import pytest

os.environ["SPOOKLING_LOCAL_MODELS"] = "0"
from spookling_memory.core import SpooklingMemory, decisions, models  # noqa: E402
from test_spookling_adapter import Stub, sess  # noqa: E402

JEV = "typesafe/jev-1.14"


class Api:
    """Stub Decisions API: gate -> a Noul p or a Choice (pick, p_top); the gate is the question id's tail."""

    def __init__(self, **answers):
        self.answers, self.requests = answers, []

    def __call__(self, model, state, questions):
        assert model == JEV and questions
        self.requests.append((state, questions))
        out = {}
        for qid, q in questions.items():
            gate = qid.split("_", 1)[1] if "_" in qid else qid.rstrip("0123456789")
            a = self.answers[gate]
            a = a(state, qid) if callable(a) else a
            if q["type"] == "noul":
                out[qid] = {"type": "noul", "noul": a}
                continue
            pick, p = a
            opts = list(q["criteria"])
            assert pick in opts, (pick, opts[:5])
            n = len(opts)
            out[qid] = {"type": "choice", "choice": pick, "confidence": (p - 1 / n) / (1 - 1 / n),
                        "probabilities": {o: p if o == pick else (1 - p) / (n - 1) for o in opts}}
        return out


@pytest.fixture
def api(monkeypatch):
    def make(**answers):
        stub = Api(**({"durable": 0.95, "replaces": 0.1, "rule": 0.05, "action": ("purchase", 0.9),
                       "object": ("bike", 0.9), "polarity": ("happened", 0.9), "r": ("new value", 0.9),
                       "class": ("purchase", 0.9), "s": 0.9} | answers))
        monkeypatch.setattr(decisions, "ask", stub)
        return stub
    return make


def mem(tags, **kw):
    return SpooklingMemory(triage=Stub(tags), decider=JEV, full_every=0, **kw)


def triaged(m):
    return [d for d in m.log.decisions if d["step"] == "triage"]


def test_durable_noul_replaces_the_taggers_confidence(api):
    stub = api(durable=lambda state, qid: 0.95 if "Lisbon" in state["message"] else 0.4)
    m = mem({"I live in Lisbon.": [dict(label="fact", key="city", value="Lisbon", confidence=0.2)],
             "I drive a Golf.": [dict(label="fact", key="car", value="Golf", confidence=0.99)]})
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "I live in Lisbon.", "I drive a Golf."))
    by = {d["value"]: d for d in triaged(m)}
    assert by["Lisbon"]["route"] == "fast" and by["Lisbon"]["confidence"] == 0.95 and by["Lisbon"]["tagger_confidence"] == 0.2
    assert (by["Golf"]["route"], by["Golf"]["flags"]) == ("slow", ["low_confidence"])  # 0.4 < 0.8, though the tagger said 0.99
    assert by["Golf"]["jev"] == {"durable": 0.4, "replaces": 0.1, "rule": 0.05}  # every answer recorded on its decision
    assert len(stub.requests) == 2 and all(set(q) == {"f0_durable", "f0_replaces", "f0_rule"} for _, q in stub.requests)


def test_replaces_noul_decides_the_swap_not_the_change_words(api):
    api(replaces=lambda state, qid: 0.9 if "Polo" in state["message"] else 0.2, r=("unclear", 0.9))
    m = mem({"Golf for me these days.": [dict(label="fact", key="car", value="Golf")],
             "My car is a Polo.": [dict(label="fact", key="car", value="Polo")],
             "Switched to a Fiat today.": [dict(label="fact", key="car", value="Fiat")]})
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "Golf for me these days."))
    m.ingest(sess(2, "2025-02-01T10:00:00Z", "My car is a Polo."))  # no change word, but Jev hears a replacement
    m.ingest(sess(3, "2025-03-01T10:00:00Z", "Switched to a Fiat today."))  # a change word, but Jev's p is 0.2
    by = {d["value"]: d for d in triaged(m)}
    assert by["Polo"]["route"] == "fast"
    assert (by["Fiat"]["route"], by["Fiat"]["flags"]) == ("slow", ["surprise"])  # below the threshold: the slow path


def test_rule_noul_pins_a_rule_the_regex_misses_and_its_fallback(api):
    p = {"Call me Dr. Rao.": 0.95, "Call me Sam.": 0.3}
    api(rule=lambda state, qid: p.get(state["message"], 0.05))
    m = mem({"Call me Dr. Rao.": [dict(label="rule", key="", value="Call me Dr. Rao")],
             "Call me Sam.": [dict(label="rule", key="", value="Call me Sam")]})
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "Call me Dr. Rao.", "Call me Sam."))
    m.settle()
    rulings = {d["value"]: d for d in m.log.decisions if d["step"] == "resolve"}
    assert rulings["Call me Dr. Rao"]["status"] == "ok" and rulings["Call me Sam"]["status"] == "disputed"
    pinned = "\n".join(m.rules())
    assert "Call me Dr. Rao." in pinned  # is_rule() says no (no general word); Jev's 0.95 pins it
    assert "Call me Sam." not in pinned  # 0.3: is_rule() alone decides, and it says no


def test_event_choices_replace_the_class_guess_and_polarity(api):
    api(action=("sell", 0.9), polarity=("planned", 0.9))
    m = mem({})
    m.ingest(sess(1, "2025-05-10T10:00:00Z", "I bought a new bike at Decathlon last weekend."))
    (d,) = [d for d in m.log.decisions if d["step"] == "event"]
    assert (d["route"], d["action_class"], d["object_class"], d["polarity"]) == ("fast", "sell", "bike", "plan")
    assert d["jev"]["action"]["probabilities"]["sell"] == 0.9 and d["jev_slow"] == []


def test_event_choices_below_the_gate_go_slow_and_polarity_falls_back(api):
    api(action=("sell", 0.5), object=("none of these", 0.9), polarity=("planned", 0.4),
        **{"class": lambda state, qid: (state["proposed"], 0.3)})
    m = mem({})
    m.ingest(sess(1, "2025-05-10T10:00:00Z", "I bought a new bike at Decathlon last weekend."))
    (d,) = [d for d in m.log.decisions if d["step"] == "event"]
    assert d["route"] == "slow" and d["jev_slow"] == ["action", "object"] and {"jev_action", "jev_object"} <= set(d["flags"])
    assert (d["action_class"], d["object_class"], d["polarity"]) == ("purchase", "bike", "did")  # the guess, polarity_of()
    m.settle()  # the class slow path asks Jev again; still unsure: the listed guess stands, disputed
    (r,) = [r for r in m.log.decisions if r["step"] == "event_resolve"]
    assert (r["route"], r["action_class"], r["status"]) == ("fast", "purchase", "disputed")


def test_a_clause_jev_sent_slow_settles_without_jev(api):
    """Written with Jev, opened without it: the doubted class stands, disputed (RuleEvents has no resolve_class)."""
    api(action=("sell", 0.5))
    m = mem({})
    m.ingest(sess(1, "2025-05-10T10:00:00Z", "I bought a new bike at Decathlon last weekend."))
    m.decider = None
    m.settle()
    (r,) = [r for r in m.log.decisions if r["step"] == "event_resolve"]
    assert (r["route"], r["action_class"], r["status"]) == ("fast", "purchase", "disputed")


@pytest.mark.parametrize("pick,want", [
    (("new value", 0.9), [("Polo", "ok")]),
    (("gone", 0.9), [(None, "ok")]),
    (("same", 0.9), [("Golf", "ok")]),
    (("unclear", 0.95), [("Polo", "disputed")]),  # unclear: the tagger's proposal stands, disputed
    (("new value", 0.5), [("Polo", "disputed")]),  # below the gate: the same
])
def test_slow_ruling_choice(api, pick, want):
    stub = api(replaces=0.1, r=pick)
    m = mem({"Golf for me these days.": [dict(label="fact", key="car", value="Golf")],
             "My car is a Polo.": [dict(label="fact", key="car", value="Polo")]})
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "Golf for me these days."))
    m.ingest(sess(2, "2025-02-01T10:00:00Z", "My car is a Polo."))
    m.settle()
    assert [(r.value, r.status) for r in m.facts.at("car", "2025-03-01")] == want
    (state, q), = [(s, q) for s, q in stub.requests if "r0" in q]
    assert state["facts"][0]["history"][0].startswith("- Golf: true from") and q["r0"]["type"] == "choice"


def test_new_class_choice_asks_the_writer_only_for_the_name(api):
    class Namer(Stub):
        def resolve_class(self, clause, word, proposed, kind, classes):
            self.named = (word, kind)
            return "repair", None

    api(action=("none of these", 0.9), **{"class": ("new class", 0.9)})
    m = SpooklingMemory(triage=(t := Namer({})), decider=JEV, full_every=0)
    m.ingest(sess(1, "2025-05-10T10:00:00Z", "I bought a new bike at Decathlon last weekend."))
    m.settle()
    (r,) = [r for r in m.log.decisions if r["step"] == "event_resolve"]
    assert t.named == ("bought", "action") and r["action_class"] == "repair" and m.classes.closed("repair")


@pytest.mark.parametrize("p,linked", [(0.9, True), (0.3, False)])
def test_same_event_noul_confirms_the_rule_link(api, p, linked):
    stub = api(action=("attend", 0.9), object=("concert", 0.9), s=p)
    m = mem({})
    m.ingest(sess(1, "2025-05-10T10:00:00Z", "I went to a Billie Eilish concert at the Wells Fargo Center last Saturday."))
    m.ingest(sess(2, "2025-05-20T10:00:00Z", "I went to a Billie Eilish concert at the Wells Fargo Center with my sister."))
    m.settle()
    rows = m.events_store.rows()
    assert len(rows) == 2 and bool(rows[1]["same_as"]) is linked
    (rec,) = [d for d in m.log.decisions if d["step"] == "same_event"]
    assert rec["noul"] == p and rec["b"] == rows[0]["event_id"]
    asked = len(stub.requests)
    m.ingest(sess(3, "2025-06-01T10:00:00Z", "How do I fix a flat tyre?"))
    m.settle()  # the rebuild reads the recorded answer: no new request for the pair
    assert len(stub.requests) == asked


def test_decider_is_off_by_default(monkeypatch):
    monkeypatch.setattr(decisions, "ask", lambda *a: pytest.fail("no decider, no Decisions call"))
    m = SpooklingMemory(triage=Stub({"I live in Lisbon.": [dict(label="fact", key="city", value="Lisbon")]}))
    m.ingest(sess(1, "2025-01-01T10:00:00Z", "I live in Lisbon.", "I bought a new bike at Decathlon last weekend."))
    m.settle()
    assert m.decider is None and "decider" not in m.config()
    assert SpooklingMemory(triage=Stub({}), decider=False).decider is None  # --adapter-arg decider=off


def test_client_goes_through_the_cache_and_records_the_cost(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(models, "CACHE_PATH", tmp_path / "cache.sqlite")
    monkeypatch.setattr(models, "USAGE", {})
    monkeypatch.setattr(decisions, "send", lambda model, state, q: sent.append(q) or (
        '{"answers": {"x": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 100, "output_tokens": 9, "cost": 4.2e-06}}',
        100, 9, 4.2e-06))
    q = {"x": {"type": "noul", "instructions": "Is `state` a greeting?"}}
    assert decisions.ask(JEV, "hello", q) == {"x": {"type": "noul", "noul": 0.9}}
    assert decisions.ask(JEV, "hello", q)["x"]["noul"] == 0.9
    u = models.USAGE[JEV]
    assert len(sent) == 1 and (u["calls"], u["hits"], u["prompt_tokens"]) == (2, 1, 200) and u["spent_usd"] == 4.2e-06
