"""The decider: Jev judges the extractor's proposals as typed questions, gated on their probability."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from . import decisions

from .triage import Ruling, sentence_of

NONE, NEW = "none of these", "new class"
POLARITY = {"happened": "did", "planned": "plan", "hypothetical": "hypothetical", "negated": "negated"}

# (statement to judge, what yes means, what no means): the question texts, in one place
DURABLE = ("This sentence states a durable fact about the user, true now.",
           "A lasting fact about the user's own life, or a lasting preference, true on `date`: where they live or work, "
           "what they own, use or do, who they are with. Something that stopped being true counts.",
           "A plan, an option being weighed, a wish, a memory of the past that changes nothing, a fact about someone "
           "else, a question, or small talk.")
REPLACES = ("The user says this value REPLACES an earlier one.",
            "The user describes a change: switched, moved, replaced, upgraded, started, left, sold, quit, or an earlier "
            "value ended.",
            "The value is stated with no sign that it replaced anything: a first mention, a repeat, or a passing mention.")
RULE = ("This sentence is a standing instruction to the assistant.",
        "An instruction to the assistant about how to answer or behave from now on: always, never, from now on, call me.",
        "The user's own goals, habits, plans or schedules, a request about the current reply only, or a statement of fact.")
SAME_EVENT = ("These two mentions describe the same event.",
              "One occurrence told twice: the same outing, purchase, visit or meeting.",
              "Two separate occurrences, even of the same kind of thing.")
ACTION_Q = "Which action class fits the action in this clause?"
OBJECT_Q = "Which object class fits the object in this clause?"
POLARITY_Q = "Did the event in this clause happen?"
POLARITY_CRITERIA = {"happened": "The user says it happened, in the past or now.",
                     "planned": "The user plans or expects it: going to, next week, booked, signed up.",
                     "hypothetical": "Only possible or wished: might, maybe, if, thinking about.",
                     "negated": "The user says it did not or will not happen: didn't, never, skipped, cancelled."}
RULING_Q = "What does the message mean for what is stored about the user?"
RULING = {"same": "Nothing stored changes: the message repeats or mentions the value in force, or talks about a plan, "
                  "an option being weighed, the past, or someone else.",
          "new value": "The proposed value is true for the user now, or corrects a stored value or its date.",
          "gone": "The stored value stopped being true and nothing replaced it.",
          "unclear": "The message does not settle what is true now."}
CLASS_Q = "Which class fits the word as it is used in the clause?"
NONE_TEXT, NEW_TEXT = "No class on the list fits.", "None of the listed classes fits; it needs a new class."
VERSION = hashlib.sha256(json.dumps([DURABLE, REPLACES, RULE, SAME_EVENT, ACTION_Q, OBJECT_Q, POLARITY_Q, POLARITY_CRITERIA,
                                     RULING_Q, RULING, CLASS_Q, NONE_TEXT, NEW_TEXT]).encode()).hexdigest()[:12]


def noul(q: tuple, **refs) -> dict:
    return {"type": "noul", "instructions": {"statement": q[0], **refs}, "criteria": {"true": q[1], "false": q[2]}}


def choice(question: str, criteria: dict, **refs) -> dict:
    return {"type": "choice", "instructions": {"question": question, **refs}, "criteria": criteria}


def class_criteria(classes, kind: str, extra: str, extra_text: str) -> dict:
    """Sorted: the cache key must not depend on set order. ponytail: a Choice takes 255 options, the seeds are 96."""
    return {c: ", ".join(sorted(classes.words[c])[:4]) for c in sorted(classes.kind) if classes.kind[c] == kind} \
        | {extra: extra_text}


def claim(label: str, key: str, value: str | None, old: str | None) -> str:
    if label == "retraction":
        return f"{key}: no longer {old or 'anything'}"
    if label == "rule":
        return f"instruction: {value}"
    return f"{key}: {value}"


def mention(d: dict) -> dict:
    return {"said": d["said_at"], "clause": d["clause"], "action": d["action"], "object": d.get("object"),
            "occurred": [d.get("occurred_from"), d.get("occurred_to")]}


class Decider:
    name = "jev"

    def __init__(self, model: str, threshold: float = 0.8, class_threshold: float = 0.6, class_confidence: float = 0.5):
        self.model, self.threshold, self.class_threshold, self.class_confidence = model, threshold, class_threshold, class_confidence
        self.version = f"{model}|{VERSION}"

    def ask(self, state, questions: dict) -> dict:
        return decisions.ask(self.model, state, questions)

    def yes(self, p: float | None, gate: str) -> bool:
        """A Noul gate: yes at p >= threshold."""
        return decisions.tally(gate, p is not None and p >= self.threshold)

    def top(self, a: dict | None) -> float:
        return float((a.get("probabilities") or {}).get(a.get("choice"), 0.0)) if a else 0.0

    def sure(self, a: dict | None, gate: str) -> bool:
        """On p_top and confidence = (p_top - 1/n) / (1 - 1/n): a high confidence floor makes class_threshold dead."""
        if not a:
            return decisions.tally(gate, False)
        p, n = self.top(a), max(len(a.get("probabilities") or {}), 2)
        conf = a.get("confidence")
        conf = (p - 1 / n) / (1 - 1 / n) if conf is None else conf  # TypeSafe's formula, if the field is missing
        return decisions.tally(gate, p >= self.class_threshold and conf >= self.class_confidence)

    def message(self, turn: str, date: str, items: list, events: list, classes) -> dict:
        """{("f", i): durable, replaces, rule Nouls} per fact tag; {("e", j): action, object, polarity} per clause."""
        facts = [(i, it) for i, it in enumerate(items) if it.label not in ("none", "hypothetical")]
        if not facts and not events:
            return {}
        state: dict = {"date": date, "message": turn}
        q: dict = {}
        if facts:
            state["facts"] = [{"sentence": sentence_of(turn, it.value or it.old), "claim": claim(it.label, it.key, it.value, it.old)}
                              for _, it in facts]
            for n, (i, _) in enumerate(facts):
                ref = {"sentence": f"`facts[{n}].sentence`", "fact": f"`facts[{n}].claim`"}
                q[f"f{i}_durable"], q[f"f{i}_replaces"] = noul(DURABLE, **ref), noul(REPLACES, **ref)
                q[f"f{i}_rule"] = noul(RULE, sentence=ref["sentence"])
        if events:
            state["events"] = [{"clause": x.clause, "action": x.action, "object": x.object} for x in events]
            acts, objs = class_criteria(classes, "action", NONE, NONE_TEXT), class_criteria(classes, "object", NONE, NONE_TEXT)
            for j, x in enumerate(events):
                clause = f"`events[{j}].clause`"
                q[f"e{j}_action"] = choice(ACTION_Q, acts, clause=clause, action=f"`events[{j}].action`")
                if x.object:
                    q[f"e{j}_object"] = choice(OBJECT_Q, objs, clause=clause, object=f"`events[{j}].object`")
                q[f"e{j}_polarity"] = choice(POLARITY_Q, POLARITY_CRITERIA, clause=clause)
        a = self.ask(state, q)
        out = {("f", i): {g: a[f"f{i}_{g}"]["noul"] for g in ("durable", "replaces", "rule")} for i, _ in facts}
        for j in range(len(events)):
            out[("e", j)] = {g: a[k] for g in ("action", "object", "polarity") if (k := f"e{j}_{g}") in a}
        return out

    def events(self, its: list, answers: dict) -> tuple[list, dict]:
        """Jev's Choices replace class guesses and rule polarity above the gate; below it the clause goes slow."""
        out, notes = [], {}
        for j, it in enumerate(its):
            a = answers.get(("e", j))
            if not a:
                out.append(it)
                continue
            new, slow = {}, []
            for kind, field in (("action", "action_class"), ("object", "object_class")):
                if kind in a:
                    if self.sure(a[kind], f"{kind}_class") and a[kind]["choice"] != NONE:
                        new[field] = a[kind]["choice"]
                    else:
                        slow.append(kind)
            if "polarity" in a and self.sure(a["polarity"], "polarity"):
                new["polarity"] = POLARITY[a["polarity"]["choice"]]
            out.append(replace(it, **new))
            notes[j] = {"jev": a, "jev_slow": slow}
        return out, notes

    def resolve(self, turn: str, date: str, slow: list[dict], history: dict[str, list[str]]) -> dict[str, dict]:
        """{decision id: Choice} for one message's slow fact items; rule items are settled by their recorded Noul."""
        asked = [d for d in slow if d["op"] != "rule"]
        if not asked:
            return {}
        state = {"date": date, "message": turn, "facts": [{"proposed": claim(d["label"], d["key"], d["value"], d["old"]),
                                                            "history": history.get(d["key"]) or ["nothing stored yet"]}
                                                           for d in asked]}
        q = {f"r{n}": choice(RULING_Q, RULING, message="`message`", proposed=f"`facts[{n}].proposed`",
                             stored=f"`facts[{n}].history`") for n in range(len(asked))}
        a = self.ask(state, q)
        return {d["id"]: a[f"r{n}"] for n, d in enumerate(asked)}

    def ruling(self, d: dict, a: dict | None):
        """Jev's answer as a Ruling. Unclear or below the gate: the tagger's proposal stands, marked disputed."""
        if d["op"] == "rule":
            p = (d.get("jev") or {}).get("rule")
            ok = self.yes(p, "resolve_rule")
            return Ruling("rule", d["value"], d["old"], d["when"], not ok, p or 0.0, f"jev rule p={p}")
        p, ok = self.top(a), self.sure(a, "resolve")
        pick = a["choice"] if a and ok else "unclear"
        why = f"jev {pick} p={p:.2f}"
        if pick == "same":
            return Ruling("none", None, None, None, False, p, why)
        if pick == "gone":
            return Ruling("retract", None, d["old"], d["when"], False, p, why)
        if pick == "new value" and d["value"]:
            return Ruling(d["op"] if d["op"] in ("assert", "correct") else "assert", d["value"], d["old"], d["when"], False, p, why)
        return Ruling(d["op"], d["value"], d["old"], d["when"], True, p, why)

    def resolve_class(self, clause: str, word: str, proposed: str, kind: str, classes) -> tuple[str | None, dict]:
        """(an existing class, NEW or None below the gate; the answer); a NEW class is named by the generative model."""
        a = self.ask({"clause": clause, "word": word, "proposed": proposed},
                     {"class": choice(CLASS_Q, class_criteria(classes, kind, NEW, NEW_TEXT), clause="`clause`", word="`word`",
                                      tagger_proposed="`proposed`")})["class"]
        return (a["choice"] if self.sure(a, "class_resolve") else None), a

    def same_event(self, d: dict, cands: list[dict]) -> list[float]:
        """p(same event) of mention d with each rule-found candidate, one request."""
        a = self.ask({"mention": mention(d), "candidates": [mention(s) for s in cands]},
                     {f"s{i}": noul(SAME_EVENT, compare=["`mention`", f"`candidates[{i}]`"]) for i in range(len(cands))})
        return [a[f"s{i}"]["noul"] for i in range(len(cands))]
