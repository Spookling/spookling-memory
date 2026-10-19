"""Against a real server on a 127.0.0.1 socket: the MCP tools (thin clients of the API) and the bash hook scripts."""
import asyncio
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
import uvicorn
from conftest import turns

from spookling_memory import mcp as M
from spookling_memory.api import create_app
from spookling_memory.portability import read_archive

HOOKS = Path(__file__).resolve().parents[2] / "hooks"


@pytest.fixture
def live(open_store):
    store = open_store("live")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()  # connections wait in the backlog until uvicorn accepts: no start-up wait needed
    server = uvicorn.Server(uvicorn.Config(create_app(store), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", store
    server.should_exit = True
    thread.join(10)
    sock.close()


def nobody() -> str:
    """A URL where nothing listens: a port just bound and released, so a connect is refused at once."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def hook(name, payload, url):
    return subprocess.run(["bash", str(HOOKS / name)], input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, "SPOOKLING_MEMORY_URL": url}, timeout=90)


def test_mcp_tools(live, tmp_path, monkeypatch):
    url, store = live
    monkeypatch.setenv("SPOOKLING_MEMORY_URL", url)
    names = {t.name for t in asyncio.run(M.server.list_tools())}
    assert names == {"memory_retrieve", "memory_ingest", "memory_compact", "memory_reset", "memory_export",
                     "memory_import", "memory_stats"}
    conv = turns(["I drive a Toyota Corolla, nothing fancy.", "From now on, always answer in metric units."])
    assert M.memory_ingest("s1", conv, "2024-05-22T10:00:00Z") == {"logged": 4}
    assert "Toyota Corolla" in M.memory_retrieve("What car do I drive?")["context"]
    assert M.memory_compact("s1")["kept"] == 1
    assert M.memory_stats()["n_events"] == 4
    path = M.memory_export(str(tmp_path / "x.spookling"))
    assert read_archive(path)[2]["counts"]["log"] == 4
    with pytest.raises(RuntimeError, match="not empty"):
        M.memory_import(path)
    r = M.memory_reset("fresh start")
    assert M.memory_retrieve("What car do I drive?")["context"] == "Nothing on record about this."
    assert M.memory_reset(undo=True)["of"] == r["id"]
    monkeypatch.setenv("SPOOKLING_MEMORY_URL", nobody())
    with pytest.raises(RuntimeError, match="no Spookling Memory server"):
        M.memory_stats()


def test_hook_scripts(live, tmp_path):
    url, store = live
    path = tmp_path / "abc.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "user", "message": {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}},
        {"type": "user", "message": {"role": "user", "content": "We decided to use Postgres 16 for the shop."}}]))
    base = {"session_id": "abc", "transcript_path": str(path)}
    out = hook("stop.sh", {**base, "hook_event_name": "Stop", "stop_hook_active": False}, url)
    assert (out.returncode, out.stdout) == (0, "")
    out = hook("pre-compact.sh", {**base, "hook_event_name": "PreCompact", "trigger": "manual"}, url)
    assert (out.returncode, out.stdout) == (0, "")
    assert len(store.mem.log.events) == 3 and [k.kind for k in store.mem.staging] == ["decision"]
    out = hook("user-prompt-submit.sh", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "What car do I drive?"}, url)
    assert out.returncode == 0
    ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Toyota Corolla" in ctx and "Postgres 16" in ctx  # the decision leads every context


@pytest.mark.parametrize("name", ["user-prompt-submit.sh", "pre-compact.sh", "stop.sh"])
def test_hook_scripts_never_block_claude_when_the_server_is_down(name):
    out = hook(name, {"session_id": "x", "prompt": "hi", "transcript_path": "/nope.jsonl"}, nobody())
    assert (out.returncode, out.stdout) == (0, "")
