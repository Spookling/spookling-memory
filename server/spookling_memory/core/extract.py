"""Event extraction: a tagger proposes spans; code checks them verbatim and decides polarity, dates and route."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import dates, timeline
from .entities import NOT_NAMES, Entities
from .events import ClassVocab, Events, event_id, stems

EXTRACTOR_VERSION = "ev1"


SENT = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.M)
SPLIT = re.compile(r"\s*(?:\bby the way\b,?|\boh,\s|;\s|,\s+and then\s|,\s+and\s|\s+and then\s|,\s+but\s|\s+but\s|,\s+also\s|\balso,\s)\s*", re.I)
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'd|I'll|we|We|we've|we're|my|My|our|Our|me)\b")
QUESTION_START = re.compile(r"^\s*(?:what|how|why|where|when|which|who|can you|could you|would you|do you|does|is there|are there|any|should i|shall)\b", re.I)
IRREGULAR = {"went", "saw", "made", "got", "bought", "took", "had", "ate", "ran", "swam", "flew", "met", "gave", "left", "began",
             "wrote", "read", "drove", "sold", "won", "lost", "found", "built", "drew", "sang", "came", "brought", "caught", "spent",
             "paid", "sent", "kept", "held", "threw", "tried", "did", "been", "visited", "attended"}


@dataclass(frozen=True)
class Clause:
    start: int  # offsets in the turn: the clause is turn[start:end], verbatim
    end: int
    text: str


def clauses(turn: str, classes: ClassVocab | None = None) -> list[Clause]:
    """First-person clauses with a verb that are not questions, split at sentences, coordination and "by the way"."""
    out = []
    for s in SENT.finditer(turn):
        pos, chunk = s.start(), s.group(0)
        cuts = [0] + [m.end() for m in SPLIT.finditer(chunk)] + [len(chunk)]
        starts = [0] + [m.start() for m in SPLIT.finditer(chunk)]
        for a, b in zip(cuts, starts[1:] + [len(chunk)], strict=False):
            piece = chunk[a:b] if b > a else ""
            lead = len(piece) - len(piece.lstrip())
            text = piece.strip().rstrip(",;.! ")
            asked = (text.endswith("?") or QUESTION_START.match(text)) and not EARLIER.search(text)  # "Remember when I...?" is a memory
            if len(text) < 6 or asked or not FIRST_PERSON.search(text):
                continue
            if not _has_verb(text, classes):
                continue
            begin = pos + a + lead
            out.append(Clause(begin, begin + len(text), text))
    return out


def _has_verb(text: str, classes: ClassVocab | None) -> bool:
    if classes is not None and classes.spans(text, "action"):
        return True
    return any(w.lower() in IRREGULAR or (len(w) > 4 and w.lower().endswith("ed")) for w in re.findall(r"[A-Za-z']+", text))


NEGATED = re.compile(r"\b(?:didn't|did not|never|haven't|have not|hasn't|wasn't|weren't|couldn't|could not|won't|no longer|not able to|failed to|skipped)\b", re.I)
PLAN = re.compile(r"\b(?:planning to|plan to|plans to|going to|gonna|will|'ll|want to|wanna|hoping to|hope to|about to|intend to|"
                  r"thinking of|thinking about|considering|looking to|aiming to|next (?:week|month|year|weekend)|tomorrow|upcoming|"
                  r"signed up to|scheduled to|booked for)\b|\w'll\b", re.I)
HYPOTHETICAL = re.compile(r"\b(?:might|may|would|could|if|maybe|perhaps|wondering (?:if|whether)|wish|torn between|or maybe)\b", re.I)
EARLIER = re.compile(r"\b(?:remember when|remember the time|back when|used to|that time (?:I|we)|the time (?:I|we))\b", re.I)


def polarity_of(clause: str) -> str:
    """did, plan, hypothetical or negated, from the clause's own words; plans and negations never count."""
    if HYPOTHETICAL.search(clause) and not NEGATED.search(clause):
        return "hypothetical"
    if NEGATED.search(clause):
        return "negated"
    if PLAN.search(clause):
        return "plan"
    return "did"


@dataclass
class EventItem:
    """One proposed event: spans copied from the turn (code checks them), classes from the closed list."""
    clause: str
    action: str
    action_class: str
    object: str | None = None
    object_class: str | None = None
    place: str | None = None
    with_: str | None = None
    amount: str | None = None
    time: str | None = None
    confidence: float = 1.0
    # the fields the time module reads: closed labels and verbatim amounts only
    tense: str | None = None  # past | present | future
    rel: str | None = None  # on | before | after | since | until | for | in_advance (measured from anchor_id)
    magnitude: str | None = None  # verbatim amount for "N days before / after" another event
    anchor_id: int | None = None  # the 1-based number of another clause of the same message it is measured from
    just: bool = False  # "just", "today", "now": on the chat's day
    polarity: str | None = None  # a decider's polarity (Jev, jev.py); None: polarity_of() decides


PLACE = re.compile(r"\b(?:at|in|to|from|near|inside)\s+((?:the\s+)?[A-Z][\w'&.-]*(?:\s+(?:of|the|de|and|&|on)\s+[A-Z][\w'&.-]*|\s+[A-Z][\w'&.-]*)*)")
WITH = re.compile(r"\bwith\s+((?:my\s+)?(?:[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?|[a-z]+(?:\s+[A-Z][\w'-]*)?))")
AMOUNT = re.compile(r"(\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:dollars|usd|euros|pounds|miles|km|kilometers|hours|minutes|kg|lbs|pounds|%))", re.I)
JUST = re.compile(r"\b(?:just|today|tonight|this morning|right now)\b", re.I)
PAST = re.compile(r"\bI (?:just |finally |recently |also )?(took|had|did|made|got|went|joined|tried|caught)\b")
EVENTISH = {"concert", "festival", "museum", "theater", "film", "sports_event", "workshop", "course", "conference", "meetup",
            "party", "wedding", "birthday", "holiday", "trip", "flight", "hike", "camping", "charity_event", "interview",
            "fitness_class", "doctor_visit", "dentist_visit", "medical", "outdoors", "grocery_store", "store", "online_order",
            "restaurant", "cafe", "bar"}
OBJECT_STOP = re.compile(r"\s+(?:at|in|on|with|for|from|to|during|last|this|yesterday|today|tonight|ago|since|before|after|and|but|"
                         r"because|which|that|while|when|so|recently|lately|again|already|finally|now|earlier|online|together|"
                         r"yet|too|as well)\b|[,.;:!?]", re.I)


class RuleEvents:
    """Deterministic spans: a listed action word, its object, a place, "with X", an amount, time words."""

    name, version = "rule-events", EXTRACTOR_VERSION

    def events(self, turns: list[str], replies: list[str], date_: str, candidates: list[list[Clause]],
               classes: ClassVocab) -> tuple[list[list[EventItem]], int]:
        out = []
        for turn, cands in zip(turns, candidates, strict=True):
            items = []
            for c in cands:
                acts = classes.spans(c.text, "action")
                if not acts:  # no listed action word: a past verb before an event-type object ("took a cooking class")
                    verb = PAST.search(c.text)
                    objs = classes.spans(c.text[verb.end():], "object") if verb else []
                    if not verb or not any(o[2] in EVENTISH for o in objs):
                        continue
                    acts = [(verb.start(1), verb.end(1), "attend")]
                a0, a1, acls = acts[0]
                rest = c.text[a1:]
                stop = OBJECT_STOP.search(rest)
                obj = rest[:stop.start()] if stop else rest
                obj = obj.strip() or None
                if obj and len(obj.split()) > 8:
                    obj = " ".join(obj.split()[:8])
                objs = classes.spans(obj, "object") if obj else []
                ocls = objs[0][2] if objs else next((x[2] for x in classes.spans(c.text, "object")), None)
                place = PLACE.search(c.text)
                with_ = WITH.search(c.text)
                amount = AMOUNT.search(c.text)
                when = dates.find_all(c.text, dates.day_of(date_ + "T12:00:00Z"), "nearest")
                pol = polarity_of(c.text)
                items.append(EventItem(c.text, c.text[a0:a1], acls, obj, ocls, place.group(1) if place else None,
                                       with_.group(1) if with_ else None, amount.group(1) if amount else None,
                                       when[0].text if when else None, tense="future" if pol == "plan" else "past",
                                       just=bool(JUST.search(c.text))))
            out.append(items)
        from .triage import Spent

        return out, Spent()


def verbatim_in(turn: str, span: str | None) -> str | None:
    """span as it appears in turn (case may differ in the model's copy), or None."""
    if not span:
        return None
    if span in turn:
        return span
    m = re.search(re.escape(span), turn, re.I)
    return m.group(0) if m else None


STATUS = {"did": "happened", "plan": "planned", "hypothetical": "hypothetical", "negated": "negated"}
BASIS = {"explicit": "stated", "relative": "relative_to_said", "for_now": "relative_to_said", "mentioned": "on_or_before_said",
         "unknown": "unknown"}


def time_item(eid: str, clause: str, src: str, polarity: str, time_words: str | None, tense: str | None, rel: str | None,
              magnitude: str | None, anchor_id: str | None, just: bool, said: date) -> timeline.Item:
    """One event as the time module's checked item: verbatim spans and closed labels only."""
    return timeline.Item(eid, clause, src, time_words, tense, STATUS[polarity], anchor_id, rel, magnitude, just, "user", said)


def occurrence(a: timeline.Anchor) -> tuple[str | None, str | None, str, str]:
    """(from, to, precision, basis); only occurrences get dates; no time words: on or before the day it was said."""
    if a.how == "unknown":
        return None, None, "unknown", "unknown"
    prec = "on_or_before" if a.lo is None else (a.gran if a.gran in ("day", "week", "month", "season", "year") else "unknown")
    return (a.lo.isoformat() if a.lo else None), (a.hi.isoformat() if a.hi else None), prec, BASIS[a.how]


def event_decision(turn_hash: str, turn: str, said_ts: str, idx: int, it: EventItem, classes: ClassVocab, writer: str,
                   version: str, created: str, anchor: timeline.Anchor | None = None, anchor_event: str | None = None) -> dict:
    """One proposed event as a decision, logged before it acts: fast, slow (a class off the list) or rejected."""
    said = dates.day_of(said_ts)
    clause = verbatim_in(turn, it.clause)
    action = verbatim_in(clause or "", it.action)
    spans = {k: verbatim_in(turn, getattr(it, k)) for k in ("object", "place", "with_", "amount", "time")}
    reject = ("clause not verbatim" if clause is None else "action not verbatim" if action is None
              else next((f"{k.rstrip('_')} not verbatim" for k in spans if getattr(it, k) and spans[k] is None), None))
    flags = []
    if not reject and not classes.closed(it.action_class):
        flags.append("new_class")
    if not reject and it.object_class and not classes.closed(it.object_class):
        flags.append("new_class")
    start = turn.find(clause) if clause else 0
    pol = it.polarity or polarity_of(clause or "")
    if anchor is None:
        anchor = timeline.anchor(time_item("x", clause or it.clause, "", pol, spans["time"], it.tense, it.rel,
                                           verbatim_in(turn, it.magnitude), None, it.just, said), said, {})
    occ_from, occ_to, prec, basis = occurrence(anchor)
    if clause and EARLIER.search(clause) and basis not in ("stated", "unknown"):  # "remember when ...": earlier, undated
        occ_from, occ_to, prec, basis = None, (said + timedelta(days=1)).isoformat(), "on_or_before", "on_or_before_said"
    route = "rejected" if reject else ("slow" if flags else "fast")
    return {"id": event_id(turn_hash, start, start + len(clause or " "), version) + f":{idx}", "step": "event",
            "source": turn_hash, "idx": idx, "version": version, "writer": writer, "created": created, "tx": said_ts,
            "route": route, "flags": ([reject] if reject else []) + flags, "clause": clause, "span_start": start,
            "span_end": start + len(clause or " "), "action": action, "action_class": it.action_class,
            "object": spans["object"], "object_class": it.object_class, "place": spans["place"], "with": spans["with_"],
            "amount": spans["amount"], "time": spans["time"], "polarity": pol,
            "occurred_from": occ_from, "occurred_to": occ_to, "occ_precision": prec, "occ_basis": basis,
            "said_at": said.isoformat(), "confidence": it.confidence, "tense": it.tense, "rel": it.rel,
            "magnitude": verbatim_in(turn, it.magnitude), "anchor_event": anchor_event, "just": it.just,
            "planned_for": ([anchor.plan.lo.isoformat() if anchor.plan.lo else None, anchor.plan.hi.isoformat() if anchor.plan.hi else None]
                            if anchor.plan else None)}


def date_turn(turn_hash: str, turn: str, said_ts: str, items: list[EventItem]) -> list[tuple[timeline.Anchor, str | None]]:
    """Each item of a turn dated in dependency order, as (Anchor, the event id it is measured from)."""
    said = dates.day_of(said_ts)
    ids = [f"c{i + 1}" for i in range(len(items))]
    xs = []
    for i, it in enumerate(items):
        ref = ids[it.anchor_id - 1] if isinstance(it.anchor_id, int) and 0 < it.anchor_id <= len(items) and it.anchor_id != i + 1 else None
        clause = verbatim_in(turn, it.clause) or it.clause
        xs.append(time_item(ids[i], clause, "", it.polarity or polarity_of(clause), verbatim_in(turn, it.time), it.tense, it.rel if ref else None,
                            verbatim_in(turn, it.magnitude) if ref else None, ref, it.just, said))
    done = timeline.anchor_all(xs)
    return [(done[ids[i]], ids[it.anchor_id - 1] if xs[i].anchor_id else None) for i, it in enumerate(items)]


PROPER = re.compile(r"(?<![\w'])((?:[A-Z][\w'&.+-]*)(?:\s+(?:of|the|de|du|and|&|on|for)\s+[A-Z][\w'&.+-]*|\s+[A-Z][\w'&.+-]*)*)")
PLACE_WORDS = {"center", "centre", "museum", "park", "stadium", "hall", "theater", "theatre", "arena", "library", "gallery",
               "beach", "lake", "mountain", "street", "avenue", "market", "cafe", "café", "restaurant", "hotel", "airport",
               "station", "university", "college", "school", "city", "town", "garden", "gardens", "bar", "pub", "club",
               "studio", "zoo", "aquarium", "trail", "canyon", "island", "bay", "valley", "county", "square", "plaza"}
ORG_WORDS = {"inc", "llc", "co", "company", "corp", "bank", "airlines", "analytics", "health", "labs", "group", "foundation",
             "society", "association", "studios", "media", "tech", "systems", "partners", "ltd"}
MONTHS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"}


def proper_spans(text: str, proper: set[str]) -> list[tuple[int, int, str]]:
    """Proper-noun spans; a single word only when capitalised somewhere other than a sentence start."""
    out = []
    for m in PROPER.finditer(text):
        name = m.group(1).rstrip(".'-")
        name = re.sub(r"^(?:The|A|An|My|Our)\s+", "", name)
        words = name.split()
        if not words or words[0] in NOT_NAMES or all(w in NOT_NAMES or w in MONTHS for w in words):
            continue
        if not any(ch.islower() for ch in name):
            continue  # an all-caps token is code or shouting ("WHERE", "SELECT"), not a name
        if len(words) == 1 and (name not in proper or name in NOT_NAMES or name in MONTHS or len(name) < 3):
            continue
        out.append((m.start(1) + m.group(1).find(name), m.start(1) + m.group(1).find(name) + len(name), name))
    return out


def kind_of(name: str, persons: set[str], after: str) -> str:
    low = {w.lower().strip(".") for w in name.split()}
    if name in persons:
        return "person"
    if low & ORG_WORDS:
        return "org"
    if low & PLACE_WORDS or re.search(r"\b(?:at|in|to|from|near)\s+(?:the\s+)?$", after):
        return "place"
    return "thing"


def entity_key(name: str) -> str:
    return "ent:" + " ".join(stems(name))


def texts_with(names, texts: list[str]) -> dict[str, int]:
    """How many texts hold each name, as `name in t`, in one pass: a scan per name was quadratic on big logs."""
    out = dict.fromkeys(names, 0)
    by: dict[str, list[str]] = {}
    for n in out:
        if n:
            by.setdefault(n[:2], []).append(n)
    if not by:
        return out
    start = re.compile("[" + re.escape("".join(sorted({k[0] for k in by}))) + "]")
    for t in texts:
        seen = set()
        for m in start.finditer(t):
            i = m.start()
            for n in by.get(t[i:i + 2], []) + by.get(t[i], []):  # a 1-character name sits under its own key
                if n not in seen and t.startswith(n, i):
                    seen.add(n)
        for n in seen:
            out[n] += 1
    return out


def build(log_events, decisions: list[dict], classes: ClassVocab, now: str, same=None) -> Events:
    """EVENTS and its tables from the LOG and the event decisions; pure. same: the decider's identity check."""
    store = Events()
    by_hash = {e.hash: e for e in log_events}
    users = [e for e in log_events if e.role == "user"]
    texts = [e.text for e in users]
    ents = Entities(texts)
    proper = {w for t in texts for w in re.findall(r"(?<=[a-z,;] )([A-Z][\w'-]+)\b", t)}
    persons = set()
    for alias, linked in ents.links.items():
        if alias.startswith("my "):
            persons |= set(linked)
    speaker = re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)?): ")
    for t in texts:
        m = speaker.match(t)
        if m:
            persons.add(m.group(1))
    # entities: multiword proper nouns, and relation and short-form aliases
    known: dict[str, tuple[str, str]] = {}  # name -> (entity id, kind)
    for t in texts:
        for a, b, name in proper_spans(t, proper):
            if name not in known:
                known[name] = (entity_key(name), kind_of(name, persons, t[:a]))
    mentions = texts_with(known, texts)
    for name, (eid, kind) in sorted(known.items()):
        store.add_entity(eid, name, kind)
        store.add_alias(name, eid, mentions[name], "name")
    for alias, linked in sorted(ents.links.items()):
        for other, support in sorted(linked.items()):
            target = known.get(other)
            if target and alias != other:
                store.add_alias(alias, target[0], support, "relation" if alias.startswith("my ") else "short_form")
    for c, ws in sorted(classes.words.items()):
        for w in sorted(ws):
            store.add_class_word(w, c, TRUSTED, "seed" if (w, c) not in classes.pending else "tagger")
    # events: one row per mention, in LOG order; same_as by identity rules
    final: dict[str, dict] = {}
    for d in decisions:
        if d.get("step") == "event" and d["route"] == "fast":
            final.setdefault(d["id"], d)
        elif d.get("step") == "event_resolve":
            final[d["of"]] = d
    rows = sorted((d for d in final.values() if d.get("route") != "rejected" and d.get("action_class") and d["source"] in by_hash),
                  key=lambda d: (by_hash[d["source"]].seq, d["span_start"], d["idx"]))
    seen: list[dict] = []
    ambiguous = 0
    for d in rows:
        e = by_hash[d["source"]]
        same_as, n_match = identity(d, seen, same)
        ambiguous += n_match > 1
        gist = " ".join(x for x in (d["action"], d.get("object") or "", f"at {d['place']}" if d.get("place") else "",
                                    f"with {d['with']}" if d.get("with") else "") if x).split()
        row = {"event_id": d["id"], "source_event": d["source"], "span_start": d["span_start"], "span_end": d["span_end"],
               "action": d["action"], "action_class": d["action_class"], "object": d.get("object"),
               "object_class": d.get("object_class"), "place": d.get("place"), "amount": _amount(d.get("amount"))[0],
               "unit": _amount(d.get("amount"))[1], "polarity": d["polarity"], "occurred_from": d["occurred_from"],
               "occurred_to": d["occurred_to"], "occ_precision": d["occ_precision"], "occ_basis": d["occ_basis"],
               "said_at": e.ts, "recorded_at": now, "same_as": same_as, "gist": " ".join(gist[:25]),
               "decision_id": d["id"], "confidence": d.get("confidence", 1.0), "writer": d["writer"],
               "status": "ambiguous" if n_match > 1 else "ok"}
        store.add_event(row)
        seen.append(d | {"_row": row})
        clause = d["clause"]
        roles = {"place": d.get("place") or "", "with": d.get("with") or "", "object": d.get("object") or ""}
        for name, (eid, _) in known.items():
            # substring test first: a regex per (event, name) overflowed re's 512-entry cache and recompiled each time
            if name in clause and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", clause):
                role = next((r for r, span in roles.items() if span and name in span), "with")
                store.link_entity(d["id"], eid, role)
        for alias in ents.links:
            if alias.startswith("my ") and alias in clause.lower():
                for other in ents.links[alias]:
                    if other in known:
                        store.link_entity(d["id"], known[other][0], "with")
        sp = speaker.match(e.text)
        if sp and sp.group(1) in known:
            store.link_entity(d["id"], known[sp.group(1)][0], "actor")
        if same_as:
            store.link(d["id"], same_as, "same_as", 1.0)
    # links between events: same occurrence day (day precision) and adjacent mentions in one session
    by_day: dict[str, list[str]] = {}
    prev: dict[str, str] = {}
    for d in rows:
        if d["occ_precision"] == "day" and d["occurred_from"]:
            by_day.setdefault(d["occurred_from"], []).append(d["id"])
        sid = by_hash[d["source"]].session_id
        if sid in prev:
            store.link(prev[sid], d["id"], "adjacent", 0.5)
        prev[sid] = d["id"]
    for ids in by_day.values():
        for a, b in zip(ids, ids[1:], strict=False):
            store.link(a, b, "same_day", 0.5)
    store.ambiguous = ambiguous
    return store


TRUSTED = 2


def _amount(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        return None, None
    unit = "$" if "$" in text else (text[m.end():].strip().lower() or None)
    return float(m.group(1).replace(",", "")), unit


def norm_object(obj: str | None) -> frozenset:
    drop = {"a", "an", "the", "my", "our", "new", "old", "some", "this", "that", "few", "of"}
    return frozenset(s for s in stems(obj or "") if s not in drop)


def compatible(a: dict, b: dict) -> bool:
    """Occurrence periods that could be one event: they overlap, or one is only "on or before" the other's end."""
    a0, a1, b0, b1 = a.get("occurred_from"), a.get("occurred_to"), b.get("occurred_from"), b.get("occurred_to")
    if a0 is None or b0 is None:
        later_said = max(a["said_at"], b["said_at"])
        fixed = b if a0 is None else a
        return fixed.get("occurred_from") is None or fixed["occurred_from"] <= later_said
    return a0 < b1 and b0 < a1


def identity(d: dict, seen: list[dict], same=None) -> tuple[str | None, int]:
    """same_as the earliest match: same action class and object, a compatible period; two matches link nothing."""
    obj = norm_object(d.get("object"))
    if d["polarity"] != "did" or not obj:
        return None, 0
    roots = {}
    for s in seen:
        if s["polarity"] == "did" and s["action_class"] == d["action_class"] and norm_object(s.get("object")) == obj \
                and compatible(s, d):
            root = s["_row"]["same_as"] or s["id"]
            roots.setdefault(root, s)
    if same is not None and roots:
        roots = {r: s for (r, s), ok in zip(roots.items(), same(d, list(roots.values())), strict=True) if ok}
    if len(roots) == 1:
        return next(iter(roots)), 1
    return None, len(roots)
