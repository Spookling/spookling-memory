"""TRIAGE: what each user turn says, as typed tags. The model only tags; code checks spans and decides the route."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

LABELS = ("fact", "preference", "rule", "correction", "retraction", "hypothetical", "none")
OPS = ("assert", "retract", "correct", "rule", "none")
WRITER = "anthropic/claude-haiku-4.5"
FIX = "That was not a JSON object of the required shape. Reply with the JSON object only."

# seed alias vocabulary, grown from keys the model proposes; no `address` key: it twinned `city` and went stale
SEED = {
    "name": ["name", "full name", "first name", "surname"],
    "birthday": ["birthday", "born", "birth date", "date of birth"],
    "hometown": ["hometown", "home town", "grew up", "grow up", "originally"],
    "city": ["city", "town", "live", "living", "lives", "based", "moved", "move", "home", "address"],
    "employer": ["employer", "company", "work", "working", "job", "employed", "workplace"],
    "job_title": ["job title", "title", "role", "position", "profession", "occupation"],
    "manager": ["manager", "boss", "report to"],
    "partner": ["partner", "girlfriend", "boyfriend", "wife", "husband", "spouse", "dating", "relationship"],
    "child": ["children", "kids", "son", "daughter"],
    "sibling": ["siblings", "sister", "brother"],
    "parent": ["mother", "father", "mum", "mom", "dad", "parents"],
    "pet": ["pet", "pets", "animal"],
    "doctor": ["doctor", "gp", "physician"],
    "dentist": ["dentist"],
    "allergy": ["allergy", "allergies", "allergic"],
    "diet": ["diet", "eat", "eating", "food"],
    "phone": ["phone", "handset", "mobile", "smartphone"],
    "laptop": ["laptop", "computer"],
    "car": ["car", "drive", "driving", "vehicle"],
    "bike": ["bike", "bicycle"],
    "sport": ["sport", "sports", "exercise", "fitness", "workout"],
    "hobby": ["hobby", "hobbies", "pastime"],
    "instrument": ["instrument", "music", "musical"],
    "team": ["team", "club", "football", "supporter", "fan"],
    "coffee": ["coffee", "café", "cafe", "drink"],
    "editor": ["editor", "ide", "code editor"],
    "notes": ["notes", "note-taking", "notes app"],
    "project": ["project", "side project"],
    "language": ["language", "languages", "speak"],
    "school": ["school", "university", "college", "degree", "studied"],
    "gym": ["gym"],
    "bank": ["bank"],
    "nationality": ["nationality", "passport", "citizenship"],
}

# code, not a model: signs that force the slow path, matched on the sentence that holds the item's value
TRIGGERS = {
    "correction": r"\b(?:actually|no wait|I meant|correction|I was wrong|not \w+(?: \w+)? but)\b",
    "no_longer": r"\bno longer\b|\bnot\b[^.!?]{0,40}\bany ?more\b",
    "rule": r"\b(?:always|never|from now on|going forward|in future|remember to|make sure (?:you|to)|don't ever"
            r"|please (?:don't|do not|stop|always|never))\b",
    "hypothetical": r"\b(?:thinking (?:about|of)|considering|torn between|between\b[^.!?]{1,40}\band|might|maybe|if I"
                    r"|planning to|plan to|hoping to|interview)",
    "past_reference": r"\b(?:used to|back when|the days when|remember(?:ing)? when)\b",
}
# a change word makes a differing value a clean change; without one it is a surprise and goes slow
CHANGE = re.compile(
    r"\b(?:switch(?:ed|ing)?|mov(?:ed|ing)|move|replac(?:ed|ing)|upgrad(?:ed|ing)|new|start(?:ed|ing)?|join(?:ed|ing)?"
    r"|left|quit|sold|bought|got rid|chang(?:ed|ing)|swap(?:ped|ping)?|took up|taken up|taking up|became|promot(?:ed|ion)"
    r"|no more|ditch(?:ed)?|migrat(?:ed|ing)|adopt(?:ed)?|register(?:ed)?|signed up|first day|last day|broke up"
    r"|split up|separated|divorced|met someone|on ice|shelved|archived|shut down|gave up|giving up|done with"
    r"|traded in|picked up|died|passed away|rehomed|car-free|stopped|finished|got an?)\b", re.I)


@dataclass
class Item:
    label: str
    key: str = ""
    value: str | None = None
    old: str | None = None
    when: str | None = None
    confidence: float = 1.0
    cues: list[str] = field(default_factory=list)


@dataclass
class Ruling:
    op: str
    value: str | None = None
    old: str | None = None
    when: str | None = None
    disputed: bool = False
    confidence: float = 1.0
    why: str = ""


@dataclass
class Spent:
    calls: int = 0
    tokens: int = 0  # prompt + completion, as the API counted them (cached replies keep their counts)
    failures: int = 0  # replies that failed the format twice


class Triage:
    """judge(turns, date, known) -> one list of Items per turn. resolve(...) -> a Ruling or None (unsettled)."""

    name = "triage"
    version = "none"

    def judge(self, turns: list[str], date: str, known: list[tuple[str, list[str], str | None]]) -> tuple[list[list[Item]], Spent]:
        raise NotImplementedError

    def resolve(self, turn: str, date: str, proposed: dict, history: list[str], flags: list[str]) -> tuple[Ruling | None, Spent]:
        raise NotImplementedError

    def events(self, turns: list[str], replies: list[str], date: str, candidates: list, classes) -> tuple[list[list], Spent]:
        """One list of extract.EventItem per user turn, for its candidate clauses."""
        raise NotImplementedError

    def resolve_class(self, clause: str, word: str, proposed: str, kind: str, classes) -> tuple[str | None, Spent]:
        """An existing class, a new class name (the only way a class enters), or None (unsettled)."""
        return None, Spent()


def call(model: str, messages: list[dict], **params):
    """Lazy hop to models.call (cached; returns text and token counts), so tests can stub it."""
    from .models import call as _call
    return _call(model, messages, **params)


SYSTEM = """You tag a person's chat messages for their long-term memory. You read the numbered user messages of one chat session and list, for each message, the durable facts it states about the user. You only tag; code decides what is true.

Labels:
- fact: something true about the user's life now: where they live or work, job title, phone, car, pet, partner, doctor, diet, sport, tools, projects, name, birthday, hometown, allergies. A passing mention of something they have now counts too ("Long day at Initech", "Washed the Golf").
- preference: a lasting taste or usual choice ("my usual order is a long black", "I prefer dark mode").
- rule: an imperative instruction to you, the assistant, about how to answer or behave from now on ("always answer in metric", "never suggest Python 2", "call me Dr. Rao"). The user's own goals, habits, plans or schedules are never rules ("I go to bed by 10:30", "read 30 minutes a day": label them fact or give no item), and neither is a request about the reply being written right now ("answer in a formal tone", "use LaTeX here").
- correction: the user says something they told you before was wrong ("actually it was March, not April").
- retraction: something stopped being true and nothing replaced it ("sold the car, car-free now", "quit my lessons", "we broke up", "the dog died").
- hypothetical: options, plans, interviews, wishes, or things not decided ("thinking about moving to York or Bath", "torn between two phones").
Give no item for small talk, requests for help, facts about other people, or memories of how things used to be that change nothing ("back when I had the Golf").

A change is one item: "switched from A to B" is label fact, value "B", old "A".

Fields of an item:
- label: fact, preference, rule, correction, retraction or hypothetical.
- key: the attribute. Use a known key when one fits; otherwise a short new lowercase snake_case noun ("gym", "bank").
- value: copied exactly from the message, same spelling and case, the whole name and nothing more ("Northwind Traders", "a terrier called Pepper"). null for a retraction.
- old: the earlier value if the message names it, copied exactly; for a retraction, what ended. Else null.
- when: the message's own words for when it became true ("today", "this week", "last month", "since March", "starting Monday"), copied exactly. Else null.
- confidence: 0 to 1, how sure you are of label, key and value together. Say so when unsure.
- cues: only for a new key, 3 to 6 everyday words someone would use when asking about it. Else [].

Example. Message [1]: "Morning! Upgraded my old phone to a Nokia X30 this week. Also, how do I sort a list in Python?"
{"turns": [{"turn": 1, "items": [{"label": "fact", "key": "phone", "value": "Nokia X30", "old": null, "when": "this week", "confidence": 0.95, "cues": []}]}]}

Reply with one JSON object and nothing else, with an entry for every message number ("items": [] when it states nothing durable):
{"turns": [{"turn": 1, "items": [...]}, {"turn": 2, "items": []}]}"""

RESOLVE = """You settle one flagged memory update for a person's long-term memory. You get one message the user wrote, its date, what a fast tagger proposed, why it was flagged, and the stored history of that attribute (each value with the dates it was true). Decide what the message means for the memory. The latest thing the user clearly states wins. If you cannot be sure, still choose the most likely reading and set "disputed" to true.

op:
- assert: the message states a value of this attribute that is true now (new, or the same as stored).
- retract: it stopped being true and nothing replaced it.
- correct: the user says something stored was wrong (a wrong value, or a wrong date).
- rule: an imperative instruction to the assistant about how to answer or behave from now on. The user's own goals, habits, plans or schedules are not rules, and neither is a request about one reply.
- none: not a durable fact about the user (a plan, an option being weighed, a memory of the past that changes nothing, someone else's fact, a question).

value, old and when are copied exactly from the message, or null. value is null for retract and none; for rule it is the instruction's words.
Reply with one JSON object and nothing else:
{"op": "assert", "value": "...", "old": null, "when": null, "disputed": false, "confidence": 0.9, "why": "one short line"}"""

FLAG_TEXT = {"correction": "it has correction words", "no_longer": "it says something is no longer so",
             "rule": "it is phrased like a standing rule", "hypothetical": "it has plan or option words",
             "past_reference": "it talks about the past", "explicit_date": "it names a date",
             "retroactive": "its date is over 30 days before the message", "postactive": "its date is in the future",
             "low_confidence": "the tagger was unsure", "surprise": "it differs from the stored value with no change word",
             "revert": "it names a value that was true before", "label:correction": "the tagger called it a correction",
             "label:rule": "the tagger called it a new rule",
             "changed": "it names part of the stored value beside another time or number"}


def _json(reply: str):
    s = reply.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, re.S)  # one fence tolerated
    return json.loads(m.group(1) if m else s)


def _opt(x, what: str) -> str | None:
    if x is None or isinstance(x, str):
        return x or None
    raise ValueError(f"{what} must be a string or null")


def _conf(x) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError("confidence must be a number")
    return min(1.0, max(0.0, float(x)))


def parse_items(reply: str, n: int) -> list[list[Item]]:
    obj = _json(reply)
    out: list[list[Item]] = [[] for _ in range(n)]
    for t in obj["turns"]:
        i = int(t["turn"]) - 1
        if not 0 <= i < n or not isinstance(t["items"], list):
            raise ValueError(f"bad turn entry {t!r}")
        for x in t["items"]:
            if not isinstance(x["label"], str) or not isinstance(x.get("key", ""), str):
                raise ValueError("label and key must be strings")
            cues = x.get("cues") or []
            if not isinstance(cues, list) or not all(isinstance(c, str) for c in cues):
                raise ValueError("cues must be a list of strings")
            out[i].append(Item(x["label"].strip().lower(), x.get("key", "").strip(), _opt(x.get("value"), "value"),
                               _opt(x.get("old"), "old"), _opt(x.get("when"), "when"), _conf(x.get("confidence", 0.0)), cues))
    return out


def parse_ruling(reply: str) -> Ruling:
    x = _json(reply)
    if x["op"] not in OPS or not isinstance(x.get("disputed", False), bool):
        raise ValueError(f"bad op {x.get('op')!r}")
    return Ruling(x["op"], _opt(x.get("value"), "value"), _opt(x.get("old"), "old"), _opt(x.get("when"), "when"),
                  x.get("disputed", False), _conf(x.get("confidence", 0.0)), str(x.get("why", ""))[:300])


class ModelTriage(Triage):
    """Both steps with the writer model, temperature 0, cached by models.py."""

    name = "model"

    def __init__(self, model: str = WRITER, max_tokens: int = 2000):
        self.model, self.max_tokens = model, max_tokens
        self.version = f"{model}|{hashlib.sha256((SYSTEM + RESOLVE).encode()).hexdigest()[:12]}|v2"

    def _ask(self, messages: list[dict], parse):
        spent = Spent()
        for _ in range(2):  # one retry; it adds turns, so the cached bad reply is not simply replayed
            r = call(self.model, messages, max_tokens=self.max_tokens)
            spent.calls += 1
            spent.tokens += r.prompt_tokens + r.completion_tokens
            try:
                return parse(r.text), spent
            except (ValueError, KeyError, TypeError, AttributeError):
                messages = messages + [{"role": "assistant", "content": r.text}, {"role": "user", "content": FIX}]
        spent.failures += 1
        return None, spent

    def judge(self, turns, date, known):
        keys = "\n".join(f"- {k}: {', '.join(words[:4])}" + (f'; now "{now}"' if now else "") for k, words, now in known)
        msgs = "\n".join(f"[{i}] {t}" for i, t in enumerate(turns, 1))
        user = f"Session date: {date}\n\nKnown keys (key: other words for it; the value stored now):\n{keys}\n\nMessages:\n{msgs}"
        items, spent = self._ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                 lambda text: parse_items(text, len(turns)))
        return (items if items is not None else [[] for _ in turns]), spent

    def resolve(self, turn, date, proposed, history, flags):
        p = proposed
        user = (f"Date: {date}\nMessage: {json.dumps(turn, ensure_ascii=False)}\n"
                f"Proposed: label={p['label']} key={p['key']} value={json.dumps(p['value'])} old={json.dumps(p['old'])}"
                f" when={json.dumps(p['when'])} confidence={p['confidence']}\n"
                f"Flagged because: {'; '.join(FLAG_TEXT.get(f, f) for f in flags) or 'no reason given'}\n"
                f"Stored history for {p['key']}:\n" + ("\n".join(history) or "(nothing stored yet)"))
        return self._ask([{"role": "system", "content": RESOLVE}, {"role": "user", "content": user}], parse_ruling)

    # events: a separate call per batch; folding it into SYSTEM would change every cached fact tag
    @property
    def event_version(self) -> str:
        return f"{self.model}|{hashlib.sha256((EVENTS + CLASS_RESOLVE).encode()).hexdigest()[:12]}|ev1"

    def events(self, turns, replies, date, candidates, classes):
        from .extract import EventItem

        listed = ", ".join(sorted(classes.kind))
        blocks = []
        for i, (t, r, cs) in enumerate(zip(turns, replies, candidates, strict=True), 1):
            clauses = "\n".join(f"  ({j}) {c.text}" for j, c in enumerate(cs, 1)) or "  (none)"
            blocks.append(f"[{i}] user: {t}\n    reply: {r[:600]}\n    clauses:\n{clauses}")
        user = f"Session date: {date}\n\nClasses (closed list): {listed}\n\nMessages:\n" + "\n".join(blocks)
        items, spent = self._ask([{"role": "system", "content": EVENTS}, {"role": "user", "content": user}],
                                 lambda text: parse_events(text, len(turns), EventItem))
        return (items if items is not None else [[] for _ in turns]), spent

    def resolve_class(self, clause, word, proposed, kind, classes):
        user = (f"Clause: {json.dumps(clause, ensure_ascii=False)}\nWord: {json.dumps(word)}\nProposed class: {proposed}\n"
                f"Kind: {kind}\nClasses (closed list): {', '.join(sorted(c for c, k in classes.kind.items() if k == kind))}")
        got, spent = self._ask([{"role": "system", "content": CLASS_RESOLVE}, {"role": "user", "content": user}],
                               lambda text: str(_json(text)["class"]).strip().lower().replace(" ", "_"))
        return got or None, spent


EVENTS = """You find the events a person mentions in their chat messages, for their long-term memory. An event is something that happened once (or is planned): they went somewhere, bought, made, met, finished, started, attended something. You get each user message, the assistant's reply to it, and candidate clauses cut from the message by code.

For each candidate clause that states an event, give one item. Copy every span EXACTLY from the message, same spelling and case; code drops any span it cannot find. Use null for a field the clause does not state.
- clause: the candidate clause, copied exactly.
- action: the verb phrase ("donated", "went to", "got back from").
- action_class: one class from the closed list that fits the action.
- object: what the action was done to or at, the whole name and nothing more ("my old coffee maker", "an amazing Billie Eilish concert").
- object_class: one class from the closed list for the object; the reply often names it. If none fits, propose a short new lowercase name: code sends it to a slower check.
- place, with, amount: as written ("the Wells Fargo Center", "my sister", "$150").
- time: the clause's own time words ("last weekend", "on May 3rd"), or null. Never compute a date.
- confidence: 0 to 1.
Give no item for small talk, requests for help, or clauses that state no event. Do not judge whether it happened: code reads "planning to", "didn't", "might".

Reply with JSON only: {"items": [[...items for message 1...], [...for message 2...], ...]}, one list per message, in order."""

CLASS_RESOLVE = """You keep the closed class list of a person's event memory. A tagger proposed a class that is not on the list. Choose an existing class from the list if one fits the word in this clause; only if none fits, name a new class (short, lowercase, snake_case, general enough to hold other things of the same kind). Reply with JSON only: {"class": "..."}"""


def parse_events(reply: str, n: int, item_cls) -> list[list]:
    data = _json(reply)
    lists = data["items"]
    if not isinstance(lists, list) or len(lists) != n:
        raise ValueError("one list per message")
    out = []
    for lst in lists:
        items = []
        for x in lst or []:
            if not isinstance(x, dict) or not x.get("clause") or not x.get("action") or not x.get("action_class"):
                continue
            items.append(item_cls(str(x["clause"]), str(x["action"]), str(x["action_class"]).strip().lower(),
                                  x.get("object"), (str(x["object_class"]).strip().lower() if x.get("object_class") else None),
                                  x.get("place"), x.get("with"), (str(x["amount"]) if x.get("amount") is not None else None),
                                  x.get("time"), float(x.get("confidence", 1.0))))
        out.append(items)
    return out


class Vocab:
    """The closed key vocabulary: maps a tagger's key to a canonical one, and a question to keys by its words."""

    def __init__(self):
        self.words: dict[str, list[str]] = {k: list(v) for k, v in SEED.items()}

    def _alias(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, ws in self.words.items():
            for w in [k.replace("_", " "), *ws]:
                out.setdefault(w.lower(), k)
        return out

    def canonical(self, label: str) -> tuple[str, str]:
        """(key, source): source is vocab (a known key), alias (a known word) or model (a new key)."""
        s = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if not s:
            return "", "none"
        if s in self.words:
            return s, "vocab"
        alias, phrase = self._alias(), s.replace("_", " ")
        if phrase in alias:
            return alias[phrase], "alias"
        parts = phrase.split()
        for p in parts:  # "phone_model" -> phone, "coffee_order" -> coffee
            if p in self.words:
                return p, "alias"
        for p in parts:
            if p in alias:
                return alias[p], "alias"
        return s, "model"

    def add(self, key: str, cues: list[str]) -> None:
        ws = self.words.setdefault(key, [])
        for w in [key.replace("_", " "), *cues]:
            w = w.strip().lower()
            if w and w not in ws:
                ws.append(w)

    def route(self, text: str) -> set[str]:
        """Keys a question names: a phrase whole, a word whole or, from 4 letters, as a prefix ("musically")."""
        low = text.lower()
        toks = re.findall(r"\w+", low)
        out = set()
        for k, ws in self.words.items():
            for w in [k.replace("_", " "), *ws]:
                if re.search(r"[\s-]", w):
                    hit = re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low) is not None
                else:
                    hit = any(t == w or (len(w) >= 4 and t.startswith(w)) for t in toks)
                if hit:
                    out.add(k)
                    break
        return out


def sentence_of(turn: str, span: str | None) -> str:
    """The sentence of turn that holds span (case-insensitive), else the whole turn."""
    if span:
        for s in re.findall(r"[^.!?]+[.!?]*", turn):
            if span.lower() in s.lower():
                return s
    return turn


def verbatim(turn: str, span: str | None) -> str | None:
    """span as it appears in turn (case may differ in the model's copy), or None if it is not there."""
    if not span:
        return None
    if span in turn:
        return span
    i = turn.lower().find(span.lower())
    return turn[i:i + len(span)] if i >= 0 else None


def mention_of(turn: str, value: str) -> str | None:
    """The longest run of value's words in turn, with a 4+ letter word: the tagger completed a short mention."""
    w = value.split()
    for n in range(len(w), 0, -1):
        for i in range(len(w) - n + 1):
            run = " ".join(w[i:i + n]).strip(",.;:!?")
            if any(len(t) >= 4 for t in re.findall(r"[^\W\d_]+", run)):
                m = re.search(rf"(?<!\w){re.escape(run)}(?!\w)", turn, re.I)
                if m:
                    return m.group(0)
    return None


def fired(sentence: str) -> list[str]:
    return [name for name, rx in TRIGGERS.items() if re.search(rx, sentence, re.I)]
