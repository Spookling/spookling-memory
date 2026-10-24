"""Decisions API client: typed questions to a decision model (Jev, typesafe/jev-1.14) on OpenRouter.

POST https://openrouter.ai/api/alpha/decisions with {"model", "state", "questions"}; the reply holds one typed
answer per question id (Noul: {"noul": p_yes}; Choice: {"choice", "probabilities", "confidence"}) and usage
(input tokens are billed, output is free; usage.cost in USD). docs/jev.md has the shapes and the guidance.

Every request goes through models.call, so it shares the sqlite cache (keyed by model, state and questions),
the single flight, the usage meter, CACHE_ONLY and the graceful credit stop. The key is OPENROUTER_API_KEY from
~/spookling-rig/jev.env when that file exists, else from the environment (models' loader).
"""
from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from . import models

URL = "https://openrouter.ai/api/alpha/decisions"
KEY_FILE = Path.home() / "spookling-rig" / "jev.env"
TIMEOUT = 120
TALLY: Counter = Counter()  # this process: calls, cached, questions, and "<gate>:ok" / "<gate>:below"
_lock = threading.Lock()


class DecisionsError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"decisions {status}: {text[:500]}")
        self.status_code = status  # models.is_credit_error reads it


def api_key() -> str:
    return models._key("OPENROUTER_API_KEY", KEY_FILE, file_first=KEY_FILE.exists())


def send(model: str, state, questions: dict) -> tuple[str, int, int, float | None]:
    """One uncached request: (the response JSON, input tokens, output tokens, usage.cost). Retries 429, 5xx, the
    in-flight-budget 402 and network errors with exponential backoff and jitter (Retry-After honoured, 60 s cap)."""
    body = json.dumps({"model": model, "state": state, "questions": questions}, ensure_ascii=False).encode()
    for attempt in range(1, models.MAX_ATTEMPTS + 1):
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})
        wait = min(60.0, 2.0**attempt)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                out = json.load(r)
            missing = set(questions) - set(out.get("answers") or {})
            if missing:
                raise DecisionsError(200, f"no answer for {sorted(missing)[:5]}")
            u = out.get("usage") or {}
            return (json.dumps(out, ensure_ascii=False), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                    float(u["cost"]) if u.get("cost") is not None else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            err = DecisionsError(e.code, text)
            if attempt == models.MAX_ATTEMPTS or not (e.code == 429 or e.code >= 500 or (e.code == 402 and "in_flight" in text)):
                raise err from None
            try:
                wait = max(wait, min(60.0, float(e.headers.get("retry-after") or 0)))
            except ValueError:
                pass
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == models.MAX_ATTEMPTS:
                raise
        time.sleep(wait / 2 + random.uniform(0, wait / 2))
    raise AssertionError("unreachable")


def ask(model: str, state, questions: dict) -> dict:
    """{question id: answer}, cached. No questions, no request."""
    if not questions:
        return {}
    r = models.call(model, [{"role": "state", "content": state}], questions=questions)
    with _lock:
        TALLY.update(calls=1, cached=int(r.cache_hit), questions=len(questions))
    return json.loads(r.text)["answers"]


def tally(gate: str, ok: bool) -> bool:
    with _lock:
        TALLY[f"{gate}:{'ok' if ok else 'below'}"] += 1
    return ok


def summary() -> str:
    t = dict(TALLY)
    gates = sorted({k.split(":")[0] for k in t if ":" in k})
    below = sum(t.get(f"{g}:below", 0) for g in gates)
    per = ", ".join(f"{g} {t.get(g + ':below', 0)}/{t.get(g + ':ok', 0) + t.get(g + ':below', 0)}" for g in gates)
    return (f"decider: {t.get('calls', 0)} calls ({t.get('cached', 0)} cached), {t.get('questions', 0)} questions;"
            f" below threshold {below} ({per or 'no gates'})")
