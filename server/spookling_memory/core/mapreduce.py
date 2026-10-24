"""Map-reduce for count, sum, compare and list questions: a model lists verbatim items; code computes the answer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from . import dates
from .dates import fmt

OPS = {
    "diff": re.compile(r"\bhow (?:many|long)\b[^?]*\b(?:days?|weeks?|months?|years?)\b[^?]*\b(?:between|since|before|after|ago|passed|elapsed|until|apart)\b"
                       r"|\bhow long (?:ago|since|before|after|between)\b", re.I),
    "sum": re.compile(r"\b(?:in total|total|altogether|combined|sum of|overall)\b|\bhow much\b[^?]*\b(?:spen[dt]|cost|paid|pay|money|earn|raise|save|saved|donat)", re.I),
    "max": re.compile(r"\b(?:most|highest|largest|biggest|longest|maximum|the best)\b", re.I),
    "min": re.compile(r"\b(?:least|lowest|smallest|shortest|minimum|fewest)\b", re.I),
    "count": re.compile(r"\bhow many\b|\bnumber of\b|\bhow often\b", re.I),
}
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100, "a dozen": 12,
         "dozen": 12, "twice": 2, "once": 1}
SYSTEM = """You read one chat between a user and an assistant and list every item in it that bears on the question. Each turn starts with its unit id in brackets, like [u12].

For each item give:
- unit: the unit id where it is said, e.g. "u12"
- quote: the exact words from that unit that state it, copied character for character, as short as possible while complete
- what: a short label for the item ("bike lights", "5K run", "Tame Impala vinyl")
- amount: the number the question needs, copied as written in the quote ("$40", "3 days", "17"), or null

Only items the user did, has, bought, attended, or stated; skip the assistant's suggestions and anything hypothetical. Do not add up, count, or compute anything. If nothing in this chat bears on the question, return an empty list.
Reply with one JSON object and nothing else: {"items": [{"unit": "u12", "quote": "...", "what": "...", "amount": "$40"}]}"""
CHECK = """You check a draft answer against a list of items found in a user's chats. The question, the items (each with a unit id, its quote and the chat date) and the draft answer are below, followed by the chats' text.
If an item that bears on the question is missing from the list, add it with its unit id and a verbatim quote from that unit. If a listed item does not bear on the question, name its unit id under "drop". Do not compute anything.
Reply with one JSON object and nothing else: {"add": [{"unit": "u12", "quote": "...", "what": "...", "amount": "$40"}], "drop": ["u7"]}"""


@dataclass(frozen=True)
class Found:
    unit: int  # LOG seq of the turn
    quote: str
    what: str
    amount: float | None
    amount_text: str | None
    day: date
    chat_day: date


def operation(question: str) -> str:
    for op in ("diff", "sum", "max", "min", "count"):
        if OPS[op].search(question):
            return op
    return "list"


def number(text: str | None) -> float | None:
    if not text:
        return None
    m = NUMBER.search(text)
    if m:
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return None
    low = text.lower()
    return next((float(v) for w, v in sorted(WORDS.items(), key=lambda x: -len(x[0])) if re.search(rf"\b{w}\b", low)), None)


def session_text(search, sid: str, trim: int) -> str:
    return "\n".join(f"[u{e.seq}] {search.line(sid, i, trim)}" for i, e in enumerate(search.sessions[sid]))


def _items(reply: str):
    s = reply.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, re.S)
    return json.loads(m.group(1) if m else s)


def keep(raw: list, turns: dict[int, object]) -> list[Found]:
    """The items whose quote is verbatim in their unit (and whose amount, if any, is in the quote)."""
    out = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        m = re.fullmatch(r"u?(\d+)", str(x.get("unit", "")).strip())
        e = turns.get(int(m.group(1))) if m else None
        quote = str(x.get("quote") or "").strip()
        if e is None or not quote or quote.lower() not in e.text.lower():
            continue
        amount_text = x.get("amount")
        amount_text = str(amount_text) if amount_text not in (None, "") else None
        if amount_text and number(amount_text) is not None and str(number(amount_text)).rstrip("0").rstrip(".") not in quote.replace(",", "") \
                and amount_text.lower() not in quote.lower():
            amount_text = None  # the amount must be in the quote
        chat_day = dates.day_of(e.ts)
        ev = [s for s in dates.find_all(quote, chat_day, "nearest") if s.kind == "relative"] or \
            [s for s in dates.find_all(e.text, chat_day, "nearest") if s.kind == "relative"]  # the turn's own date words
        out.append(Found(e.seq, quote, str(x.get("what") or quote)[:80], number(amount_text), amount_text,
                         ev[0].vf if ev else chat_day, chat_day))
    return out


def extract(call, model: str, search, question: str, sessions: list[str], trim: int = 300) -> tuple[list[Found], int, int]:
    """(items, calls, failures): one cached call per chat."""
    found, calls, failures = [], 0, 0
    for sid in sessions:
        turns = {e.seq: e for e in search.sessions[sid]}
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Question: {question}\nChat date: {search.sessions[sid][0].ts[:10]}\n\n{session_text(search, sid, trim)}"}]
        calls += 1
        try:
            found += keep(_items(call(model, msgs, max_tokens=1500).text).get("items", []), turns)
        except (ValueError, KeyError, TypeError, AttributeError):
            failures += 1
    seen, out = set(), []
    for f in found:  # one item per (unit, quote)
        if (f.unit, f.quote.lower()) not in seen:
            seen.add((f.unit, f.quote.lower()))
            out.append(f)
    return out, calls, failures


def combine(op: str, items: list[Found], today: date) -> str:
    """The computed result, in words, from the items alone (code, not a model)."""
    if not items:
        return ""
    amounts = [f for f in items if f.amount is not None]
    if op == "count":
        labels = sorted({f.what.lower() for f in items})
        return f"count = {len(labels)} distinct items ({', '.join(labels)})"
    if op == "sum" and amounts:
        return f"total = {_n(sum(f.amount for f in amounts))} (" + " + ".join(_n(f.amount) for f in amounts) + \
            f" over {len(amounts)} items with an amount" + (f"; {len(items) - len(amounts)} items had none)" if len(items) > len(amounts) else ")")
    if op in ("max", "min") and amounts:
        best = (max if op == "max" else min)(amounts, key=lambda f: f.amount)
        return f"{op} = {_n(best.amount)} ({best.what}, u{best.unit})"
    if op == "diff":
        days = sorted({f.day for f in items})
        a, b = (days[0], days[-1]) if len(days) >= 2 else (days[0], today)
        d = (b - a).days
        return f"between {fmt(a)} and {fmt(b)}: {d} days (about {d / 7:.1f} weeks, {d / 30.44:.1f} months)"
    return f"{len(items)} items listed"


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def render(op: str, result: str, items: list[Found]) -> str:
    lines = [f"[u{f.unit}, chat of {fmt(f.chat_day)}" + (f", event {fmt(f.day)}" if f.day != f.chat_day else "") + f'] {f.what}: "{f.quote}"'
             + (f" (amount {f.amount_text})" if f.amount_text else "") for f in sorted(items, key=lambda f: (f.day, f.unit))]
    return (f"<computed>\nQuestion type: {op}. Computed by code from the {len(items)} items below: {result}\n</computed>\n"
            "<items>\n" + "\n".join(lines) + "\n</items>\n"
            "Phrase the answer from the computed result and the items; cite the unit ids you rely on, like [u12].")


def selfcheck(call, model: str, search, question: str, answer: str, items: list[Found], sessions: list[str],
              trim: int = 300) -> tuple[list[Found], bool]:
    """(corrected items, changed) after one cached checking call. Unparseable: the items as they were."""
    turns = {e.seq: e for sid in sessions for e in search.sessions[sid]}
    listed = "\n".join(f'[u{f.unit}] {f.what}: "{f.quote}" (chat {f.chat_day})' for f in items)
    chats = "\n\n".join(session_text(search, sid, trim) for sid in sessions)
    msgs = [{"role": "system", "content": CHECK},
            {"role": "user", "content": f"Question: {question}\n\nItems:\n{listed}\n\nDraft answer: {answer}\n\nChats:\n{chats}"}]
    try:
        obj = _items(call(model, msgs, max_tokens=1500).text)
    except (ValueError, KeyError, TypeError, AttributeError):
        return items, False
    drop = {int(m.group(1)) for d in obj.get("drop") or [] if (m := re.fullmatch(r"u?(\d+)", str(d).strip()))}
    added = keep(obj.get("add") or [], turns)
    have = {(f.unit, f.quote.lower()) for f in items}
    new = [f for f in items if f.unit not in drop] + [f for f in added if (f.unit, f.quote.lower()) not in have]
    return new, len(new) != len(items) or bool(drop)
