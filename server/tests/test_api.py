"""The HTTP API through FastAPI's TestClient, and the hooks' server side on a fake Claude Code transcript."""
import json

import pytest
from bench.adapters.spookling.recall import ABSTAIN
from conftest import HISTORY, NOW, TAGS, Stub, turns
from fastapi.testclient import TestClient

from spookling_memory import hooks
from spookling_memory.api import create_app


def client(store):
    return TestClient(create_app(store), base_url="http://127.0.0.1:7447")


def test_every_endpoint(open_store):
    store = open_store("a")
    c = client(store)
    for sid, ts, users in HISTORY:
        assert c.post("/v1/ingest", json={"id": sid, "ts": ts, "turns": turns(users)}).json() == {"logged": 2 * len(users)}
    kept = c.post("/v1/compact", json={"session_id": "s3"}).json()
    assert kept["kept"] == 1 and "always answer in metric units" in kept["block"]
    archive = c.get("/v1/export")
    assert archive.status_code == 200 and archive.headers["content-type"] == "application/gzip"
    q = {"question": "What car do I drive?", "ts": NOW}
    got = c.post("/v1/retrieve", json=q).json()
    assert set(got) == {"context", "digest", "strong"} and "Toyota Corolla" in got["context"] and got["strong"]
    stats = c.get("/v1/stats").json()
    assert stats["n_events"] == 12 and stats["reset"] is None and stats["before_reset"] == 0

    other = client(open_store("b"))
    restored = other.post("/v1/import", content=archive.content, headers={"content-type": "application/octet-stream"})
    assert restored.json() == {"events": 12, "judgements": stats["n_decisions"]}
    assert other.post("/v1/retrieve", json=q).json() == got
    again = other.post("/v1/import", content=archive.content, headers={"content-type": "application/octet-stream"})
    assert again.status_code == 409 and "not empty" in again.json()["detail"]

    r = c.post("/v1/reset", json={"note": "fresh start"}).json()  # at now: after every turn above
    assert r["step"] == "reset" and r["note"] == "fresh start"
    assert c.post("/v1/retrieve", json={"question": "What car do I drive?"}).json()["context"] == ABSTAIN
    assert c.get("/v1/stats").json()["reset"]["id"] == r["id"]
    assert c.post("/v1/reset/undo", json={}).json()["of"] == r["id"]
    assert "Toyota Corolla" in c.post("/v1/retrieve", json={"question": "What car do I drive?"}).json()["context"]
    assert c.post("/v1/reset/undo", json={}).status_code == 409


def test_guards(open_store):
    store = open_store()
    assert TestClient(create_app(store), base_url="http://memory.attacker.example").get("/v1/stats").status_code == 400
    c = client(store)
    assert c.post("/v1/reset", content='{"note": ""}', headers={"content-type": "text/plain"}).status_code == 415
    assert c.post("/v1/reset/undo").status_code == 415  # no content type: a page could send that without a preflight
    assert c.post("/v1/import", content=b"junk", headers={"content-type": "application/octet-stream"}).status_code == 409
    assert c.post("/v1/hook/session-start", json={}).status_code == 404
    bad = {"id": "x", "turns": [{"role": "system", "content": "hi"}]}
    assert c.post("/v1/ingest", json=bad).status_code == 422
    assert c.get("/v1/stats").json()["n_events"] == 0


def write_transcript(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


TRANSCRIPT = [
    {"type": "summary", "summary": "Car chat"},
    {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>ignore</local-command-caveat>"}},
    {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
    {"type": "user", "message": {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."}},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "..."}, {"type": "text", "text": "Noted."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
    {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Anything else?"}]}},
    {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "a subagent's prompt"}},
    {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text":
        '<recalled-data now="x">\nuser: I drive a Toyota Corolla\n</recalled-data>\nFrom now on, always answer in metric units.'}]}},
]


def test_hooks_log_the_transcript_and_recall_for_the_prompt(open_store, tmp_path):
    store = open_store()
    c = client(store)
    path = tmp_path / "abc.jsonl"
    write_transcript(path, TRANSCRIPT)
    assert hooks.turns(path) == [
        {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."},
        {"role": "assistant", "content": "Noted.\n\nAnything else?"},
        {"role": "user", "content": "From now on, always answer in metric units."}]  # our own recalled block stripped
    base = {"session_id": "abc", "transcript_path": str(path), "cwd": str(tmp_path)}
    prompt = {**base, "hook_event_name": "UserPromptSubmit", "prompt": "What car do I drive?"}
    assert c.post("/v1/hook/user-prompt-submit", json=prompt).json() == {}  # empty memory: nothing injected

    stop = {**base, "hook_event_name": "Stop", "stop_hook_active": False}
    assert c.post("/v1/hook/stop", json=stop).json() == {}
    assert [(e.session_id, e.role) for e in store.mem.log.events] == [("abc#0", "user"), ("abc#0", "assistant"), ("abc#0", "user")]
    c.post("/v1/hook/stop", json=stop)
    assert len(store.mem.log.events) == 3  # the same transcript again: nothing new

    out = c.post("/v1/hook/user-prompt-submit", json=prompt).json()["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit" and "Toyota Corolla" in out["additionalContext"]
    assert len(out["additionalContext"]) <= hooks.CAP

    write_transcript(path, TRANSCRIPT + [
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Metric it is."}]}},
        {"type": "user", "message": {"role": "user", "content": "We decided to use Postgres 16 for the shop."}}])
    pre = {**base, "hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": ""}
    assert c.post("/v1/hook/pre-compact", json=pre).json() == {}
    assert [e.session_id for e in store.mem.log.events][3:] == ["abc#3", "abc#3"]  # the new tail, then kept
    assert {k.kind for k in store.mem.staging} == {"rule", "decision"}

    gone = {**stop, "transcript_path": str(tmp_path / "missing.jsonl")}
    assert c.post("/v1/hook/stop", json=gone).json() == {}  # no transcript: nothing to log, no error


@pytest.mark.parametrize("prompt", ["", "   "])
def test_an_empty_prompt_injects_nothing(open_store, prompt):
    c = client(open_store())
    assert c.post("/v1/hook/user-prompt-submit", json={"session_id": "x", "prompt": prompt}).json() == {}


def test_a_missing_model_key_is_a_503_and_the_server_lives(open_store):
    class NoKey(Stub):
        def judge(self, turns, date, known):
            raise SystemExit("OPENROUTER_API_KEY is not set")  # what bench.models does

    store = open_store()
    c = client(store)
    store.mem.triage = NoKey(TAGS)
    r = c.post("/v1/ingest", json={"id": "s1", "ts": "2024-05-22T10:00:00Z", "turns": turns(["I drive a Toyota Corolla, nothing fancy."])})
    assert r.status_code == 503 and "OPENROUTER_API_KEY" in r.json()["detail"]
    store.mem.triage = Stub(TAGS)
    assert c.post("/v1/ingest", json={"id": "s2", "ts": "2024-05-23T10:00:00Z", "turns": turns(["How do I roast peppers?"])}).status_code == 200
    assert c.get("/v1/stats").json()["n_events"] == 4  # the first session waited in memory and was written, tagged
