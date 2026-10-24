"""EVENTS, ENTITIES, ALIASES, CLASSES and their links: derived tables, rebuilt from LOG and DECISIONS, never updated."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

import snowballstemmer

# REFERENCES are documentation: the log lives in its own database, and code checks every source_event first
SCHEMA = """
CREATE TABLE events (
  event_id       TEXT PRIMARY KEY,
  source_event   TEXT NOT NULL REFERENCES log(event_hash),
  span_start     INT NOT NULL, span_end INT NOT NULL,
  action         TEXT NOT NULL,
  action_class   TEXT NOT NULL,
  object         TEXT,
  object_class   TEXT,
  place          TEXT, amount REAL, unit TEXT,
  polarity       TEXT NOT NULL CHECK (polarity IN ('did', 'plan', 'hypothetical', 'negated')),
  occurred_from  TEXT, occurred_to TEXT,
  occ_precision  TEXT NOT NULL CHECK (occ_precision IN ('day', 'week', 'month', 'season', 'year', 'on_or_before', 'unknown')),
  occ_basis      TEXT NOT NULL CHECK (occ_basis IN ('stated', 'relative_to_said', 'on_or_before_said', 'unknown')),
  said_at        TEXT NOT NULL,
  recorded_at    TEXT NOT NULL,
  same_as        TEXT REFERENCES events(event_id),
  gist           TEXT NOT NULL,
  decision_id    TEXT NOT NULL, confidence REAL NOT NULL, writer TEXT NOT NULL, status TEXT DEFAULT 'ok',
  CHECK (span_start >= 0 AND span_end > span_start)
);
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, canonical TEXT, kind TEXT CHECK (kind IN ('person', 'place', 'org', 'thing')));
CREATE TABLE aliases  (alias TEXT, entity_id TEXT, support INT, rule TEXT);
CREATE TABLE classes  (word TEXT, class TEXT, support INT, rule TEXT);
CREATE TABLE event_entities (event_id TEXT, entity_id TEXT, role TEXT CHECK (role IN ('actor', 'with', 'object', 'place')));
CREATE TABLE event_links (a TEXT, b TEXT, kind TEXT CHECK (kind IN ('same_day', 'adjacent', 'before', 'same_as')), weight REAL);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events: derived, insert only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events: derived, insert only'); END;
"""
POLARITY = ("did", "plan", "hypothetical", "negated")
CLASSES_FILE = Path(__file__).with_name("classes.json")
DETERMINERS = {"a", "an", "the", "my", "our", "your", "his", "her", "their", "some", "this", "that", "these", "those", "any", "no"}
TRUST = 2  # a new word for a listed class is applied once seen this many times

_stemmer = snowballstemmer.stemmer("english")
_TOKEN = re.compile(r"[\w+'-]+")


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    return _stemmer.stemWord(word.lower().strip("'-"))


def stems(text: str) -> list[str]:
    return [stem(w) for w in _TOKEN.findall(text.lower())]


def seed_classes() -> dict[str, dict]:
    """class -> {"kind": action|object, "words": [...]} from the closed seed list."""
    return {c["class"]: {"kind": c["kind"], "words": c["words"]} for c in json.loads(CLASSES_FILE.read_text())["classes"]}


def event_id(source: str, start: int, end: int, version: str) -> str:
    return hashlib.sha256(f"{source}\x00{start}\x00{end}\x00{version}".encode()).hexdigest()[:24]


class ClassVocab:
    """The closed class list and the words filed under it. match() reads stemmed whole words or phrases."""

    def __init__(self, seed: dict[str, dict] | None = None):
        self.kind: dict[str, str] = {}
        self.words: dict[str, set[str]] = {}  # class -> its words (applied)
        self.pending: dict[tuple[str, str], int] = {}  # (word, class) -> times proposed, until TRUST
        for c, v in (seed if seed is not None else seed_classes()).items():
            self.kind[c] = v["kind"]
            self.words[c] = set(v["words"]) | {c.replace("_", " ")}
        self._rx = None

    def closed(self, cls: str | None) -> bool:
        return cls is not None and cls in self.kind

    def add_class(self, cls: str, kind: str, words: list[str]) -> None:
        """The slow path only: the one way a class not on the closed list enters."""
        if kind not in ("action", "object"):
            raise ValueError(f"class kind must be action or object, not {kind!r}")
        self.kind[cls] = kind
        self.words.setdefault(cls, set()).update(set(words) | {cls.replace("_", " ")})
        self._rx = None

    def propose_word(self, word: str, cls: str) -> bool:
        """Applied once seen TRUST times; False for an unlisted class (the slow path's) or a word below the line."""
        if not self.closed(cls) or not word.strip():
            return False
        w = word.strip().lower()
        if w in self.words[cls]:
            return True
        n = self.pending.get((w, cls), 0) + 1
        self.pending[(w, cls)] = n
        if n >= TRUST:
            self.words[cls].add(w)
            self._rx = None
            return True
        return False

    def _index(self):
        if self._rx is None:
            phrases: dict[tuple[str, ...], set[str]] = {}
            for c, ws in self.words.items():
                for w in ws:
                    key = tuple(stems(w))
                    if key:
                        phrases.setdefault(key, set()).add(c)
            self._rx = phrases
        return self._rx

    def match(self, text: str, kind: str | None = None) -> list[tuple[int, int, str]]:
        """(token start, token end, class) for every listed word or phrase in text, longest first, no overlaps."""
        toks = stems(text)
        index = self._index()
        longest = max((len(k) for k in index), default=0)
        out, used = [], set()
        for n in range(longest, 0, -1):
            for i in range(len(toks) - n + 1):
                key = tuple(toks[i:i + n])
                if key in index and not used & set(range(i, i + n)):
                    for c in sorted(index[key]):
                        if kind is None or self.kind[c] == kind:
                            out.append((i, i + n, c))
                    used |= set(range(i, i + n))
        return sorted(out)

    def classes_in(self, text: str, kind: str | None = None) -> set[str]:
        return {c for _, _, c in self.match(text, kind)}

    def spans(self, text: str, kind: str | None = None) -> list[tuple[int, int, str]]:
        """match() in character offsets; an action word after a determiner ("a play") is a noun, not an action."""
        toks = list(_TOKEN.finditer(text))
        out = []
        for i, j, c in self.match(text, kind):
            if j - 1 >= len(toks):
                continue
            if self.kind.get(c) == "action" and i > 0 and toks[i - 1].group(0).lower() in DETERMINERS:
                continue
            out.append((toks[i].start(), toks[j - 1].end(), c))
        return out


class Events:
    """The store: one in-memory SQLite database of six tables."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def add_event(self, row: dict) -> None:
        cols = ("event_id", "source_event", "span_start", "span_end", "action", "action_class", "object", "object_class",
                "place", "amount", "unit", "polarity", "occurred_from", "occurred_to", "occ_precision", "occ_basis",
                "said_at", "recorded_at", "same_as", "gist", "decision_id", "confidence", "writer", "status")
        self.db.execute(f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        tuple(row.get(c) for c in cols))

    def add_entity(self, entity_id: str, canonical: str, kind: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?)", (entity_id, canonical, kind))

    def add_alias(self, alias: str, entity_id: str, support: int, rule: str) -> None:
        self.db.execute("INSERT INTO aliases VALUES (?,?,?,?)", (alias, entity_id, support, rule))

    def add_class_word(self, word: str, cls: str, support: int, rule: str) -> None:
        self.db.execute("INSERT INTO classes VALUES (?,?,?,?)", (word, cls, support, rule))

    def link_entity(self, event_id: str, entity_id: str, role: str) -> None:
        self.db.execute("INSERT INTO event_entities VALUES (?,?,?)", (event_id, entity_id, role))

    def link(self, a: str, b: str, kind: str, weight: float = 1.0) -> None:
        self.db.execute("INSERT INTO event_links VALUES (?,?,?,?)", (a, b, kind, weight))

    def rows(self, sql: str = "SELECT * FROM events ORDER BY said_at, span_start", args=()) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args)]

    def get(self, event_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return dict(r) if r else None

    def root(self, event_id: str) -> str:
        """The first mention of an event: follow same_as to its end."""
        seen = set()
        while event_id not in seen:
            seen.add(event_id)
            r = self.db.execute("SELECT same_as FROM events WHERE event_id=?", (event_id,)).fetchone()
            if not r or not r[0]:
                return event_id
            event_id = r[0]
        return event_id

    def clusters(self, event_ids) -> dict[str, list[str]]:
        """same_as clusters: root -> its mentions. Counts and sums run over these, never over rows."""
        out: dict[str, list[str]] = {}
        for e in event_ids:
            out.setdefault(self.root(e), []).append(e)
        return out

    def entities_of(self, event_id: str) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.db.execute("SELECT entity_id, role FROM event_entities WHERE event_id=?", (event_id,))]

    def all_aliases(self) -> list[tuple[str, str, int]]:
        return [(r[0], r[1], r[2]) for r in self.db.execute("SELECT alias, entity_id, support FROM aliases")]

    def count(self, table: str) -> int:
        if table not in ("events", "entities", "aliases", "classes", "event_entities", "event_links"):
            raise ValueError(table)
        return self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
