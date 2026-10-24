"""Codex uses the existing store, hook endpoints, tail logging and compaction; no alternate memory engine."""
import json

from fastapi.testclient import TestClient

from spookling_memory.api import create_app
from spookling_memory.hooks import codex_turns


def message(role, text, phase=None):
    return {"type": "response_item", "payload": {"type": "message", "role": role, "phase": phase,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}


def test_codex_capture_recall_and_compaction(open_store, tmp_path):
    path = tmp_path / "rollout.jsonl"
    entries = [
        {"type": "session_meta", "payload": {"instructions": "not a fact"}},
        message("developer", "injected reference facts must not become conversation"),
        message("user", "# AGENTS.md instructions for /project\nNot the user's words"),
        message("user", "<environment_context>\nlocal configuration\n</environment_context>"),
        message("user", "I drive a Toyota Corolla, nothing fancy."),
        {"type": "event_msg", "payload": {"type": "user_message", "message": "duplicate of the user turn"}},
        message("assistant", "I will check a file.", "commentary"),
        message("assistant", "private reasoning", "analysis"),
        {"type": "response_item", "payload": {"type": "function_call_output", "output": "not memory"}},
        message("assistant", "Noted.", "final_answer"),
    ]
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    assert codex_turns(path) == [
        {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."},
        {"role": "assistant", "content": "Noted."},
    ]
    store = open_store()
    client = TestClient(create_app(store), base_url="http://127.0.0.1:7447")
    base = {"session_id": "codex-chat", "harness": "codex", "transcript_path": str(path)}
    assert client.post("/v1/hook/stop", json={**base, "last_assistant_message": "Noted."}).json() == {}
    assert len(store.mem.log.events) == 2
    assert all(e.agent == "codex" for e in store.mem.log.events)
    client.post("/v1/hook/stop", json=base)
    assert len(store.mem.log.events) == 2, 'repeated Stop must not duplicate turns'
    recalled = client.post("/v1/hook/user-prompt-submit", json={**base, "prompt": "What car do I drive?"}).json()
    assert "Toyota Corolla" in recalled["hookSpecificOutput"]["additionalContext"]
    entries.append(message("user", "From now on, always answer in metric units."))
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    assert client.post("/v1/hook/pre-compact", json=base).json() == {}
    assert len(store.mem.log.events) == 3
    assert 'rule' in {k.kind for k in store.mem.staging}


def test_codex_final_reply_can_lag_the_transcript(open_store, tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(json.dumps(message("user", "I drive a Toyota Corolla, nothing fancy.")), encoding="utf-8")
    store = open_store()
    client = TestClient(create_app(store), base_url="http://127.0.0.1:7447")
    body = {"session_id": "codex-chat", "harness": "codex", "transcript_path": str(path), "last_assistant_message": "Noted."}
    client.post("/v1/hook/stop", json=body)
    assert [e.role for e in store.mem.log.events] == ["user", "assistant"]
