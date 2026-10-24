"""Entity table: aliases linked by rules, no model; links only change ranking, stored text is never rewritten."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

RELATIONS = ("sister|brother|mom|mum|mother|dad|father|wife|husband|partner|boyfriend|girlfriend|fiancé|fiancée|fiance|fiancee"
             "|friend|best friend|colleague|coworker|co-worker|boss|manager|son|daughter|cousin|aunt|uncle|niece|nephew"
             "|grandma|grandpa|grandmother|grandfather|roommate|neighbor|neighbour|therapist|doctor|dentist|coach|teacher"
             "|mentor|dog|cat|puppy|kitten|baby|kid|kids|sibling|in-law|sister-in-law|brother-in-law|stepmom|stepdad")
NAME = r"((?:Dr|Mr|Mrs|Ms|Miss|Prof)\.? [A-Z][a-z]+|[A-Z][a-z]+(?: [A-Z][a-z]+)?)"  # titles stay with the name
PATTERNS = [
    re.compile(rf"\b[Mm]y (?:younger |older |little |big |old |new |best |close )?({RELATIONS}) (?:named |called )?{NAME}"),
    # "my girlfriend, Toby and I" is a list, not "my girlfriend Toby": the comma form refuses a following "and"
    re.compile(rf"\b[Mm]y (?:younger |older |little |big |old |new |best |close )?({RELATIONS}), {NAME}(?!,? (?:and|&|or)\b)"),
    re.compile(rf"\b{NAME}, [Mm]y (?:younger |older |little |big |old |new |best |close )?({RELATIONS})\b"),
]
NOT_NAMES = {"I", "I'm", "I've", "I'd", "I'll", "The", "This", "That", "My", "Hey", "Hi", "Hello", "Thanks", "Thank", "Yes", "No",
             "Oh", "So", "And", "But", "Also", "Just", "Well", "Sure", "Okay", "Ok", "Wow", "Monday", "Tuesday", "Wednesday",
             "Thursday", "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December", "What", "How", "When", "Where", "Which", "Who", "Why",
             "Can", "Could", "Would", "Should", "Do", "Does", "Did", "Is", "Are", "It", "It's", "We", "You", "They", "He", "She"}
TITLES = {"Dr", "Mr", "Mrs", "Ms", "Miss", "Prof"}
SPEAKER = re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)?): ")


class Entities:
    def __init__(self, texts: list[str]):
        """texts: the user's turns, in order (LoCoMo turns keep their "Speaker: " prefix)."""
        pairs: dict[tuple[str, str], int] = Counter()
        names: Counter = Counter()
        # a proper noun: capitalised somewhere other than the start of a sentence ("Thankfully, my son" is not a name)
        proper = {w for t in texts for w in re.findall(r"(?<=[a-z,;] )([A-Z][a-z]+)\b", t)}
        for t in texts:
            seen = set()
            m = SPEAKER.match(t)
            if m:
                names[m.group(1)] += 2  # a speaker is a known name
                proper.update(m.group(1).split())
            for rx in PATTERNS:
                for g in rx.finditer(t):
                    rel, name = (g.group(1), g.group(2)) if rx is not PATTERNS[-1] else (g.group(2), g.group(1))
                    words = [w for w in name.replace(".", "").split() if w not in TITLES]
                    if not words or words[0] in NOT_NAMES or not all(w in proper for w in words):
                        continue
                    seen.add((f"my {rel.lower()}", name))
                    names[name] += 1
            for pair in seen:
                pairs[pair] += 1
        self.links: dict[str, dict[str, int]] = defaultdict(dict)  # alias -> {linked alias: support}
        by_rel: dict[str, Counter] = defaultdict(Counter)
        for (rel, name), n in pairs.items():
            by_rel[rel][name] += n
        for rel, cnt in by_rel.items():
            (top, n), *rest = cnt.most_common()
            if rest and n < 2 * rest[0][1]:
                continue  # under the confidence line: "my friend" names two people about equally
            self.links[rel][top] = n
            self.links[top][rel] = n
        known = [n for n in names if (" " in n or names[n] >= 2) and all(w in proper | TITLES for w in n.replace(".", "").split())]
        for short in {w for t in texts for w in re.findall(r"\b[A-Z][a-z]{2,}\b", t)} - NOT_NAMES:
            longer = [n for n in known if n != short and n.startswith(short)]
            if len(longer) == 1:
                support = sum(1 for t in texts if re.search(rf"\b{short}\b", t))
                self.links[short][longer[0]] = support
                self.links[longer[0]][short] = support
        self._rx = re.compile(r"(?<!\w)(" + "|".join(sorted(map(re.escape, self.links), key=len, reverse=True)) + r")(?!\w)") \
            if self.links else None

    def expand(self, question: str) -> list[tuple[str, float]]:
        """Extra queries for the aliases a question names: (linked alias, weight 1.0 trusted, 0.5 provisional)."""
        if self._rx is None:
            return []
        out = {}
        for m in self._rx.finditer(question):
            for other, support in self.links[m.group(1)].items():
                if other.lower() not in question.lower():
                    out[other] = max(out.get(other, 0.0), 1.0 if support >= 2 else 0.5)
        return sorted(out.items())

    def pairs(self) -> list[tuple[str, str, int]]:
        return sorted({tuple(sorted((a, b))) + (n,) for a, d in self.links.items() for b, n in d.items()})
