"""FACTS: bitemporal rows, changed only by insert or close; status 'disputed' never hides or blocks a row."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta

FOREVER = "9999-12-31"
SUBJECT = "user"  # ponytail: one subject; 'user.partner', 'repo:x' when other subjects are triaged
# several values at once, keyed by (attribute, item); a fixed list, not yet judged per new key
MULTI_VALUED = frozenset({"allergy", "language", "child", "sibling"})

SCHEMA = f"""
CREATE TABLE facts (
  row_id INTEGER PRIMARY KEY, subject TEXT NOT NULL, attribute TEXT NOT NULL, item TEXT NOT NULL DEFAULT '',
  value TEXT, value_kind TEXT NOT NULL DEFAULT 'value',
  valid_from TEXT NOT NULL, valid_to TEXT NOT NULL DEFAULT '{FOREVER}', from_precision TEXT NOT NULL DEFAULT 'day',
  recorded_at TEXT NOT NULL, superseded_at TEXT NOT NULL DEFAULT '{FOREVER}',
  source_event TEXT NOT NULL, decision_id TEXT NOT NULL, confidence REAL NOT NULL, writer TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok', kind TEXT NOT NULL DEFAULT 'fact',
  CHECK (valid_from < valid_to), CHECK (recorded_at < superseded_at), CHECK ((value_kind = 'none') = (value IS NULL)),
  CHECK (value_kind IN ('value', 'none')), CHECK (status IN ('ok', 'disputed')));
CREATE UNIQUE INDEX one_open_value ON facts(subject, attribute, item)
  WHERE valid_to = '{FOREVER}' AND superseded_at = '{FOREVER}';
CREATE TRIGGER facts_no_delete BEFORE DELETE ON facts BEGIN SELECT RAISE(ABORT, 'facts: insert or close only'); END;
CREATE TRIGGER facts_no_edit BEFORE UPDATE OF row_id, subject, attribute, item, value, value_kind, valid_from, valid_to,
  from_precision, recorded_at, source_event, decision_id, confidence, writer, status, kind ON facts
  BEGIN SELECT RAISE(ABORT, 'facts: insert or close only'); END;
CREATE TRIGGER facts_close_once BEFORE UPDATE OF superseded_at ON facts WHEN OLD.superseded_at <> '{FOREVER}'
  BEGIN SELECT RAISE(ABORT, 'facts: a row is closed once'); END;
CREATE TABLE fact_stats (subject TEXT, attribute TEXT, item TEXT, value TEXT, n_confirmed INTEGER NOT NULL,
  last_confirmed_at TEXT NOT NULL, last_decision TEXT, PRIMARY KEY (subject, attribute, item, value));
"""
# sequenced in valid time, current in transaction time: must return nothing
OVERLAPS = f"""SELECT a.attribute, a.item, a.row_id, b.row_id FROM facts a JOIN facts b
  ON a.subject = b.subject AND a.attribute = b.attribute AND a.item = b.item AND a.row_id < b.row_id
  WHERE a.superseded_at = '{FOREVER}' AND b.superseded_at = '{FOREVER}'
    AND a.valid_from < b.valid_to AND b.valid_from < a.valid_to"""
_DROP = {"a", "an", "the", "my", "our"}


class IntegrityError(AssertionError):
    pass


@dataclass(frozen=True)
class Row:
    row_id: int
    subject: str
    attribute: str
    item: str
    value: str | None
    value_kind: str
    valid_from: str
    valid_to: str
    from_precision: str
    recorded_at: str
    superseded_at: str
    source_event: str
    decision_id: str
    confidence: float
    writer: str
    status: str
    kind: str


def toks(s: str) -> list[str]:
    return [w for w in re.findall(r"\w+", s.lower()) if w not in _DROP]


def norm(s: str) -> str:
    return " ".join(toks(s))


def same(mention: str, stored: str) -> bool:
    """A mention names a stored value when its words are a subset of the value's; a longer mention is another value."""
    m = set(toks(mention))
    return bool(m) and m <= set(toks(stored))


def next_day(iso: str) -> str:
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


class Facts:
    def __init__(self, src: "Facts | None" = None):
        self.db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if src is None:
            self.db.executescript(SCHEMA)
            self.surfaces: dict[tuple[str, str, str], set[str]] = {}  # (attribute, item, norm value) -> words used for it
            self.rules: list[dict] = []
        else:
            src.db.backup(self.db)
            self.surfaces = {k: set(v) for k, v in src.surfaces.items()}
            self.rules = list(src.rules)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def _rows(self, sql: str, args=()) -> list[Row]:
        return [Row(**dict(r)) for r in self.db.execute(sql, args)]

    def believed(self, attr: str, item: str | None = None) -> list[Row]:
        """Rows believed now (superseded_at = forever), oldest valid_from first."""
        if item is None:
            return self._rows("SELECT * FROM facts WHERE subject=? AND attribute=? AND superseded_at=? ORDER BY item, valid_from",
                              (SUBJECT, attr, FOREVER))
        return self._rows("SELECT * FROM facts WHERE subject=? AND attribute=? AND item=? AND superseded_at=? ORDER BY valid_from",
                          (SUBJECT, attr, item, FOREVER))

    def at(self, attr: str, start: str, end: str | None = None) -> list[Row]:
        """Rows in force on `start`, or overlapping [start, end)."""
        end = end or next_day(start)
        return self._rows("SELECT * FROM facts WHERE subject=? AND attribute=? AND superseded_at=? AND valid_from < ?"
                          " AND ? < valid_to ORDER BY item, valid_from", (SUBJECT, attr, FOREVER, end, start))

    def in_force(self, day: str) -> list[Row]:
        return self._rows("SELECT * FROM facts WHERE superseded_at=? AND valid_from <= ? AND ? < valid_to"
                          " ORDER BY attribute, item", (FOREVER, day, day))

    def attributes(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT attribute FROM facts WHERE superseded_at=? ORDER BY 1", (FOREVER,))]

    def stats_for(self, attr: str, item: str, value: str | None) -> tuple[int, str | None, str | None]:
        """(confirmations of this value, the day of the last, its decision); a new value starts at zero."""
        r = self.db.execute("SELECT n_confirmed, last_confirmed_at, last_decision FROM fact_stats WHERE subject=? AND attribute=?"
                            " AND item=? AND value=?", (SUBJECT, attr, item, norm(value or ""))).fetchone()
        return (r[0], r[1], r[2]) if r else (0, None, None)

    def all_rows(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM facts ORDER BY row_id")]

    def _insert(self, attr, item, value, a, b, tx, src: dict) -> None:
        self.db.execute(
            "INSERT INTO facts (subject, attribute, item, value, value_kind, valid_from, valid_to, from_precision,"
            " recorded_at, source_event, decision_id, confidence, writer, status, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SUBJECT, attr, item, value, "none" if value is None else "value", a, b, src["precision"], tx,
             src["source"], src["id"], src["confidence"], src["writer"], src["status"], src["kind"]))

    def put(self, attr: str, item: str, value: str | None, a: str, b: str, tx: str, meta: dict, insert: bool = True) -> None:
        """Make the believed history of (attr, item) say `value` over [a, b) (cut it, if not insert)."""
        for r in self._rows("SELECT * FROM facts WHERE subject=? AND attribute=? AND item=? AND superseded_at=?"
                            " AND valid_from < ? AND ? < valid_to", (SUBJECT, attr, item, FOREVER, b, a)):
            if r.recorded_at >= tx:
                raise IntegrityError(f"{attr}: a change recorded at {tx} cannot close row {r.row_id} recorded at {r.recorded_at}")
            self.db.execute("UPDATE facts SET superseded_at=? WHERE row_id=?", (tx, r.row_id))
            keep = {"precision": r.from_precision, "source": r.source_event, "id": r.decision_id,
                    "confidence": r.confidence, "writer": r.writer, "status": r.status, "kind": r.kind}
            if r.valid_from < a:
                self._insert(attr, item, r.value, r.valid_from, a, tx, keep)
            if b < r.valid_to:
                self._insert(attr, item, r.value, b, r.valid_to, tx, keep | {"precision": "day"})
        if insert:
            self._insert(attr, item, value, a, b, tx, meta)

    def touch(self, attr: str, item: str, value: str | None, tx: str, decision: str | None = None) -> None:
        """Seen again: counters only, never a new row."""
        self.db.execute("INSERT INTO fact_stats VALUES (?,?,?,?,1,?,?) ON CONFLICT DO UPDATE SET n_confirmed = n_confirmed + 1,"
                        " last_confirmed_at = excluded.last_confirmed_at, last_decision = excluded.last_decision",
                        (SUBJECT, attr, item, norm(value or ""), tx[:10], decision))

    def add_surface(self, attr: str, item: str, stored: str, said: str) -> None:
        self.surfaces.setdefault((attr, item, norm(stored)), set()).add(said)

    def check(self) -> None:
        bad = self.db.execute(OVERLAPS).fetchall()
        if bad:
            raise IntegrityError(f"overlapping believed rows (attribute, item, row, row): {[tuple(x) for x in bad][:5]}")

    def apply(self, d: dict) -> str:
        """One decision applied: what it did, for the log. Pure function of (store, decision)."""
        op, attr = d["op"], d["key"]
        if op == "none":
            return "none"
        if op == "rule":
            self.rules.append({"text": d["value"], "source": d["source"], "said": d["tx"][:10], "status": d["status"],
                               "p_rule": (d.get("jev") or {}).get("rule")})  # Jev's standing-instruction Noul, if asked
            return "rule"
        meta = {k: d[k] for k in ("source", "id", "confidence", "writer", "status", "kind", "precision")}
        if attr in MULTI_VALUED:
            return self._apply_multi(d, meta)
        value = d["value"] if op != "retract" else None
        rows, said, vf = self.believed(attr, ""), d["tx"][:10], d["vf"]
        if d.get("old"):
            for r in rows:
                if r.value and same(d["old"], r.value):
                    self.add_surface(attr, "", r.value, d["old"])
        if op == "correct" and value is not None:
            done = self._correct(attr, rows, value, d, meta, said)
            if done:
                return done
        started = [r for r in rows if r.valid_from <= said]
        if started and vf <= started[-1].valid_from:
            vf = next_day(started[-1].valid_from)  # a plain update never erases the old value's whole period
        cur = next((r for r in rows if r.valid_from <= vf < r.valid_to), None)
        if cur is not None and (cur.value is None if value is None else (cur.value is not None and same(value, cur.value))):
            if value:
                self.add_surface(attr, "", cur.value, value)
            if value is not None and set(toks(value)) != set(toks(cur.value)):
                return "mention"  # only some of its words: no new row, no confirmation either
            self.touch(attr, "", cur.value, d["tx"], d.get("id"))
            return "confirm"
        b = min((r.valid_from for r in rows if r.valid_from > vf), default=FOREVER)
        if vf >= b:
            return "skip"
        self.put(attr, "", value, vf, b, d["tx"], meta)
        if value:
            self.add_surface(attr, "", value, value)
        return ("retract" if value is None else "update") if cur else ("none-row" if value is None else "insert")

    def _correct(self, attr, rows, value, d, meta, said) -> str | None:
        """A correction of the past: a retroactive sequenced update."""
        tx, vf = d["tx"], d["vf"]
        if d.get("old"):  # "it's X, not Y": Y's whole period becomes X
            o = next((r for r in reversed(rows) if r.value and same(d["old"], r.value)), None)
            if o:
                self.put(attr, "", value, o.valid_from, o.valid_to, tx, meta | {"precision": o.from_precision})
                self.add_surface(attr, "", value, value)
                return "correct"
        if d.get("when"):  # "X since March, not April": X's start moves
            t = next((r for r in reversed(rows) if r.value and same(value, r.value)), None)
            if t is None:
                b = min((r.valid_from for r in rows if r.valid_from > vf), default=FOREVER)
                self.put(attr, "", value, vf, b, tx, meta)
            elif vf < t.valid_from:
                self.put(attr, "", value, vf, t.valid_to, tx, meta)
            elif vf > t.valid_from:
                prev = next((r for r in rows if r.valid_to == t.valid_from), None)
                self.put(attr, "", prev.value if prev else None, t.valid_from, vf, tx,
                         meta | {"kind": prev.kind if prev else meta["kind"]}, insert=prev is not None)
            self.add_surface(attr, "", value, value)
            return "correct"
        cur = next((r for r in rows if r.valid_from <= said < r.valid_to), None)  # "actually it's X": the current row was wrong
        if cur and not (cur.value and same(value, cur.value)):
            self.put(attr, "", value, cur.valid_from, cur.valid_to, tx, meta | {"precision": cur.from_precision})
            self.add_surface(attr, "", value, value)
            return "correct"
        return None

    def _apply_multi(self, d: dict, meta: dict) -> str:
        """Several values at once, one row history per item; `none` lives at item ''. Corrections act as asserts."""
        attr, tx, vf = d["key"], d["tx"], d["vf"]
        if d["op"] in ("assert", "correct"):
            item = norm(d["value"])
            rows = self.believed(attr, item)
            cur = next((r for r in rows if r.valid_from <= vf < r.valid_to), None)
            if cur is not None:
                self.add_surface(attr, item, cur.value, d["value"])
                self.touch(attr, item, cur.value, tx, d.get("id"))
                return "confirm"
            b = min((r.valid_from for r in rows if r.valid_from > vf), default=FOREVER)
            self.put(attr, item, d["value"], vf, b, tx, meta)
            self.add_surface(attr, item, d["value"], d["value"])
            self.put(attr, "", None, vf, FOREVER, tx, meta, insert=False)  # an item ends "has none"
            return "insert"
        items = [r for r in self.at(attr, vf) if r.item]
        gone = [r for r in items if d.get("old") and r.value and same(d["old"], r.value)] or ([] if d.get("old") else items)
        for r in gone:
            self.put(attr, r.item, None, max(vf, next_day(r.valid_from)), FOREVER, tx, meta, insert=False)
        if not [r for r in self.at(attr, vf) if r.item] and not [r for r in self.at(attr, vf) if r.value is None]:
            self.put(attr, "", None, vf, FOREVER, tx, meta)
        return "retract"

    def view_lines(self) -> list[str]:
        """Believed rows (the history as best known now; recall reads every version), as JSON."""
        return [json.dumps({k: v for k, v in asdict(r).items() if k not in ("superseded_at",)}, ensure_ascii=False)
                for r in self._rows("SELECT * FROM facts WHERE superseded_at=? ORDER BY row_id", (FOREVER,))]
