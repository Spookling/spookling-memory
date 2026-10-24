"""Query rewriting for complex questions (v2; Mohammed's guards, 13 Oct).

One cached writer-model call turns a complex question (complex_triggers) into up to 3 search queries plus entity
names and synonyms. Guards, all code: the original question is always query one and is never replaced; at most 3
rewrites; a rewrite that shares no content word or entity with the question is dropped; the caller fuses the
hit lists by RRF so rewrites can only add candidates below the original's, and the reranker scores every
candidate against the ORIGINAL question, never a rewrite. The time window is the original question's.
"""
from __future__ import annotations

import json
import re

SYSTEM = """You help search a person's past chat messages. Turn the question into 2 or 3 short search queries that would find the user's own messages holding the answer (split a two-part question into its parts; name the things to look for), and list the entity names and synonyms worth searching for. Keep every name from the question. Do not answer the question.
Reply with one JSON object and nothing else: {"queries": ["...", "..."], "entities": ["..."], "synonyms": ["..."]}"""
MAX_REWRITES = 3


def _content(text: str) -> set[str]:
    from .recall import words

    return set(words(text))


def guard(question: str, raw: dict) -> list[str]:
    """The rewrites that survive: at most 3, each sharing a content word or a named entity with the question."""
    q_words, q_low = _content(question), question.lower()
    ents = [e for e in raw.get("entities") or [] if isinstance(e, str) and e.strip() and e.lower() in q_low]
    out = []
    for q in raw.get("queries") or []:
        if not isinstance(q, str) or not q.strip() or q.strip().lower() == q_low.strip():
            continue
        if _content(q) & q_words or any(e.lower() in q.lower() for e in ents):
            out.append(q.strip())
    syn = [s for s in raw.get("synonyms") or [] if isinstance(s, str) and s.strip()]
    if ents and syn and len(out) < MAX_REWRITES:
        out.append(" ".join(ents + syn))  # anchored by an entity the question names
    return out[:MAX_REWRITES]


def rewrite(call, model: str, question: str) -> list[str]:
    try:
        reply = call(model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Question: {question}"}],
                     max_tokens=400).text.strip()
        m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", reply, re.S)
        return guard(question, json.loads(m.group(1) if m else reply))
    except (ValueError, TypeError, AttributeError):
        return []
