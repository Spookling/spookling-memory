"""compact(): keep verbatim, by code, what a summary must not erase; a summary can drop a rule, this cannot."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import dates

LEAD = re.compile(r"\b(?:from now on|from here on|going forward|for the rest of (?:this|the) (?:session|chat|conversation|project)"
                  r"|standing (?:rule|instruction|order)|ground rule|house rule|one rule|new rule|important rule|rule for (?:today|now)"
                  r"|in future|from today|until I say otherwise)\b", re.I)
# an instruction to the assistant: (always / never / don't / please ...) and then a verb of how to answer or act
IMPERATIVE = re.compile(r"^(?:please |and |also |so |ok,? |important[,:]? )?(?:always |never |only |don't |do not |please |just )*"
                        r"(?:write|give|reply|respond|answer|end|start|begin|address|call|use|keep|store|put|format|express|list|refer|"
                        r"stop|make sure|remember to|suggest|recommend|mention|point|schedule|include|add|send|show|say|spell|round|"
                        r"convert|book|plan|avoid|ask|cite|quote|translate|explain|describe|limit|stick|run|deploy|test|name|label|"
                        r"sign|greet|reference|measure|report|express|state|treat)\b", re.I)
GENERAL = re.compile(r"\b(?:every|always|never|only|all|any|each|whenever|no (?:more|longer)|from now on)\b", re.I)
CANT = re.compile(r"\b(?:we|you) (?:can't|cannot|can not|must not|mustn't|aren't allowed to|are not allowed to) use\b", re.I)
DECISION = re.compile(r"\b(?:we decided|i decided|decided to|let's go with|we'll go with|we're going with|go with|agreed|"
                      r"the plan is|we'll use|we will use|settled on|final answer is)\b", re.I)
VALUE = re.compile(r"(?:[$€£]\s?\d|\b\d[\d,.]*\s?(?:%|ms|s\b|sec|seconds|minutes|hours|days|weeks|kb|mb|gb|tb|px|km|mi|kg|lb|"
                   r"usd|eur|gbp|req/s|rps|qps|k\b|m\b)|\bv?\d+\.\d+(?:\.\d+)?\b|\bport \d+\b)", re.I)
PATH = re.compile(r"(?:https?://\S+|(?:~|\.{1,2})?/[\w.-]+(?:/[\w.-]+)+|\b[\w-]+\.(?:py|js|ts|tsx|json|yaml|yml|toml|md|sql|sh|go|rs|java|"
                  r"rb|cfg|ini|env|txt|csv)\b|\b(?:feat|fix|chore|release|hotfix)/[\w.-]+)")
NAME = re.compile(r"\b(?:codename|code name|branch|project|repo|bucket|table|service|cluster|database|db|host|server|channel)\s+[\w./-]*[A-Za-z0-9]",
                  re.I)
FAILED = re.compile(r"\b(?:tried|attempted)\b.*\b(?:didn't|did not|doesn't|does not|failed|no luck|not work|broke|crashed|"
                    r"no difference|made it worse|didn't help)\b|\b(?:didn't work|doesn't work|did not work|no luck|dead end|"
                    r"ruled out|made it worse|rolled back|reverted)\b", re.I)
OPEN = re.compile(r"\b(?:todo|to-do|still need to|still have to|need to (?:figure out|decide|check|fix|pick|choose)|next step|"
                  r"open question|pending|follow up on|tbd|not decided|haven't decided|haven't figured|left to do|blocked on|"
                  r"waiting on|we still need)\b", re.I)
# a sentence ends at . ! ? followed by space or the line's end (so "values.yaml" and "v1.2" stay whole), quotes kept
SENT = re.compile(r"[^\n]+?(?:[.!?]+[\"'\u201d)\]]*(?=\s|$)|$)", re.M)


@dataclass(frozen=True)
class Kept:
    kind: str  # rule | decision | failed | open | value | date | path | name
    text: str  # verbatim
    seq: int  # LOG pointer
    day: str


ABBREV = re.compile(r"\b(Dr|Mr|Mrs|Ms|Prof|St|Jr|Sr|vs|etc|e\.g|i\.e|No)\.", re.I)


def sentences(text: str) -> list[str]:
    guarded = ABBREV.sub(lambda m: m.group(1) + "\u2024", text)  # "Dr." does not end a sentence
    out = [s.strip().replace("\u2024", ".") for s in SENT.findall(guarded) if s.strip()]
    merged = []
    i = 0
    while i < len(out):  # "One standing rule before we go on:" leads the sentence after it
        s = out[i]
        if s.endswith(":") or re.search(r":\s*$", s):
            if i + 1 < len(out):
                s = f"{s} {out[i + 1]}"
                i += 1
        merged.append(s)
        i += 1
    return merged


def is_rule(s: str) -> bool:
    body = s.split(":", 1)[1].strip() if ":" in s and LEAD.search(s.split(":", 1)[0]) else s
    if LEAD.search(s) or CANT.search(s):
        return True
    return bool(IMPERATIVE.search(body) and GENERAL.search(body))


def extract(events) -> list[Kept]:
    """Every rule, decision, value, date, path and name the user stated in these turns, verbatim."""
    out: list[Kept] = []
    for e in events:
        if e.role != "user":
            continue
        day = dates.day_of(e.ts)
        for s in sentences(e.text):
            if len(s) < 8:
                continue
            if is_rule(s):
                kind = "rule"
            elif DECISION.search(s):
                kind = "decision"
            elif FAILED.search(s):
                kind = "failed"
            elif OPEN.search(s):
                kind = "open"
            elif PATH.search(s):
                kind = "path"
            elif NAME.search(s):
                kind = "name"
            elif VALUE.search(s):
                kind = "value"
            elif any(sp.kind == "absolute" for sp in dates.find_all(s, day, "nearest")):
                kind = "date"
            else:
                continue
            out.append(Kept(kind, s, e.seq, e.ts[:10]))
    return out
