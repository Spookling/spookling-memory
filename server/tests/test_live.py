"""Against a real server on a 127.0.0.1 socket: the MCP tools (thin clients of the API) and both sets of hook scripts
(hooks/*.js with node, any OS; hooks/*.sh with bash and curl, macOS and Linux)."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from conftest import turns

from spookling_memory import mcp as M
from spookling_memory.api import create_app
from spookling_memory.portability import read_archive

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
RUNNERS = [
    pytest.param(("node", ".js"), id="js", marks=pytest.mark.skipif(not shutil.which("node"), reason="no node")),
    pytest.param(("bash", ".sh"), id="sh", marks=pytest.mark.skipif(
        os.name == "nt" or not shutil.which("curl"), reason="the .sh hooks are for macOS and Linux")),
]


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


def hook(runner, name, payload, url):
    """Run one hook script as Claude Code's exec form does: the interpreter, the script path as one argument."""
    exe, ext = runner
    return subprocess.run([exe, str(HOOKS / (name + ext))], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "SPOOKLING_MEMORY_URL": url}, timeout=90)


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
    for down in (nobody(), "http://10.255.255.1:7447"):  # refused, and dropped (see below)
        monkeypatch.setenv("SPOOKLING_MEMORY_URL", down)
        t = time.monotonic()
        with pytest.raises(RuntimeError, match="no Spookling Memory server"):
            M.memory_stats()
        assert time.monotonic() - t < 5


@pytest.mark.parametrize("runner", RUNNERS)
def test_hook_scripts(live, tmp_path, runner):
    url, store = live
    path = tmp_path / "abc.jsonl"
    path.write_text(encoding="utf-8", data="".join(json.dumps(e) + "\n" for e in [
        {"type": "user", "message": {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}},
        {"type": "user", "message": {"role": "user", "content": "We decided to use Postgres 16 for the shop."}}]))
    base = {"session_id": "abc", "transcript_path": str(path)}
    out = hook(runner, "stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False}, url)
    assert (out.returncode, out.stdout) == (0, "")
    out = hook(runner, "pre-compact", {**base, "hook_event_name": "PreCompact", "trigger": "manual"}, url)
    assert (out.returncode, out.stdout) == (0, "")
    assert len(store.mem.log.events) == 3 and [k.kind for k in store.mem.staging] == ["decision"]
    out = hook(runner, "user-prompt-submit", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "What car do I drive?"}, url)
    assert out.returncode == 0
    ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Toyota Corolla" in ctx and "Postgres 16" in ctx  # the decision leads every context


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("name", ["user-prompt-submit", "pre-compact", "stop"])
@pytest.mark.parametrize("where", ["refused", "dropped"])
def test_hook_scripts_never_block_claude_when_the_server_is_down(name, runner, where):
    """refused: nothing listens. dropped: the connect is never answered, as a dead port on WSL's mirrored network
    behaves (10.255.255.1 is not routed; where there is no route at all it fails at once, which also passes)."""
    url = nobody() if where == "refused" else "http://10.255.255.1:7447"
    t = time.monotonic()
    out = hook(runner, name, {"session_id": "x", "prompt": "hi", "transcript_path": "/nope.jsonl"}, url)
    assert (out.returncode, out.stdout) == (0, "") and time.monotonic() - t < 5
