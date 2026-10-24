"""INDEX: the page the model sees first, rendered by formula from FACTS and RULES, never by a model
(research/33 E.1). Rules pinned first; then the values in force now, grouped by type, ranked by how often
they were confirmed and then how recently recall used them; capped. Noise control (v1.1): a preference or hobby
said only once is never shown, and the page leads a context only when it holds MIN_STRONG facts confirmed twice.
v4: the page lists only facts confirmed twice or more."""
from __future__ import annotations

from datetime import date

from .dates import fmt
from .facts import Facts

TASTES = ("hobby",)  # attributes treated like preferences by the noise rule below
MIN_STRONG = 1  # v5: the page shows whenever a fact is confirmed twice (it lists only those); before v5, 3


def hidden(facts: Facts, r) -> bool:
    """A preference or hobby said once (never confirmed) is too likely to be tagger noise to show anywhere."""
    return (r.kind == "preference" or r.attribute in TASTES) and facts.stats_for(r.attribute, r.item, r.value)[0] == 0


def strong(facts: Facts, now: str) -> int:
    """How many values in force now were confirmed at least twice."""
    return sum(facts.stats_for(r.attribute, r.item, r.value)[0] >= 2 for r in facts.in_force(now))


def render(facts: Facts, usage: dict, now: str, cap: int = 200) -> str:
    """The page without rules: v3 pins rules in their own <rules> block at the top of every context."""
    lines: list[str] = []
    groups: dict[str, list] = {}
    for r in facts.in_force(now):  # v4: confirmed twice or more; a once-said hobby misled the reader (LoCoMo conv-50-q046)
        if facts.stats_for(r.attribute, r.item, r.value)[0] >= 2:
            groups.setdefault(r.kind, []).append(r)
    for kind, title in (("fact", "Facts in force now:"), ("preference", "Preferences:")):
        rows = sorted(groups.get(kind, []), key=lambda r: (r.attribute, r.item))
        rows.sort(key=lambda r: (facts.stats_for(r.attribute, r.item, r.value)[0], usage.get((r.attribute, r.item), (0, ""))[1]),
                  reverse=True)
        if rows:
            lines.append(title)
        for r in rows:
            n = facts.stats_for(r.attribute, r.item, r.value)[0]
            since = ("on or before " if r.from_precision == "on_or_before" else "") + fmt(date.fromisoformat(r.valid_from))
            lines.append(f"- {r.attribute}: {r.value if r.value is not None else 'none any more'} (since {since}"
                         + (f"; confirmed {n}x" if n else "") + ("; disputed" if r.status == "disputed" else "") + ")")
    return "\n".join(lines[:cap])
