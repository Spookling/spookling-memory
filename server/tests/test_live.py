"""Against a real server on a socket: the MCP tools and both sets of hook scripts."""
import asyncio
import json
import re
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

from spookling_memory import hooks, mcp as M
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
    sock.listen()  # connections wait in the backlog until uvicorn accepts
    server = uvicorn.Server(uvicorn.Config(create_app(store), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", store
    server.should_exit = True
    thread.join(10)
    sock.close()


def nobody() -> str:
    """A port just bound and released: a connect is refused at once."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def hook(runner, name, payload, url, **env):
    """As Claude Code's exec form runs it: the interpreter, the script path as one argument."""
    exe, ext = runner
    return subprocess.run([exe, str(HOOKS / (name + ext))], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, "SPOOKLING_MEMORY_URL": url, **env}, timeout=90)


def test_mcp_tools_find_their_folders_own_server(tmp_path, monkeypatch):
    monkeypatch.delenv("SPOOKLING_MEMORY_URL", raising=False)
    monkeypatch.setenv("SPOOKLING_MEMORY_FOLDER", str(tmp_path))
    assert M.base() == "http://127.0.0.1:7447"  # no server.json yet: the old fixed port
    (tmp_path / "server.json").write_text('{"port": 7731, "pid": 1}')
    assert M.base() == "http://127.0.0.1:7731"
    monkeypatch.setenv("SPOOKLING_MEMORY_URL", "http://127.0.0.1:9999")
    assert M.base() == "http://127.0.0.1:9999"  # a server you run yourself wins


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


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_codex_ghost_and_memory_hooks_use_the_existing_server(live, tmp_path):
    url, store = live
    from test_codex import message

    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        message("user", "I drive a Toyota Corolla, nothing fancy."),
        message("assistant", "Noted.", "final_answer"),
        message("user", "From now on, always answer in metric units."),
    ]), encoding="utf-8")
    base = {"session_id": "codex-live", "transcript_path": str(path)}
    runner = ("node", ".cjs")
    start = hook(runner, "spookling-codex", {**base, "hook_event_name": "SessionStart"}, url)
    said = plain(json.loads(start.stdout)["systemMessage"])
    assert start.returncode == 0 and said[0] == "spookling ready · local server connected"
    assert has(said, "0 messages remembered") and has(said, "0 facts in force · 0 standing rules"), said  # an empty store
    assert has(said, store.folder.name), "the folder the server opened"
    for event in ("Stop", "PreCompact"):
        result = hook(runner, "spookling-codex", {**base, "hook_event_name": event}, url)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert len(store.mem.log.events) == 3 and all(e.agent == "codex" for e in store.mem.log.events)
    result = hook(runner, "spookling-codex", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "What car do I drive?"}, url)
    reply = json.loads(result.stdout)
    ctx = reply["hookSpecificOutput"]["additionalContext"]
    assert "Toyota Corolla" in ctx and "metric" in ctx
    said = plain(reply["systemMessage"])
    assert said[0] == f"spookling active · {spoken(hooks.counts(ctx))} recalled", said  # the server's own count of ctx
    assert has(said, f"≈{kilo(len(ctx))} of the 10k characters of room"), said
    start = hook(runner, "spookling-codex", {**base, "hook_event_name": "SessionStart"}, url)
    assert has(plain(json.loads(start.stdout)["systemMessage"]), "3 messages remembered"), "the store as it is now"


def plain(message: str) -> list[str]:
    """A ghost message as Codex shows it, without colour: the words line, then the ghost's rows with words beside."""
    return [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in message.split("\n")]


def has(lines: list[str], words: str) -> bool:
    return any(line.endswith(words) for line in lines)


def kilo(v: int) -> str:
    """ghost.kilo in hooks/ghost.js."""
    return f"{v / 1000:.{0 if v >= 10000 else 1}f}k" if v >= 1000 else str(v)


def spoken(c: dict) -> str:
    """pulse.said in hooks/pulse.js: "1 fact and 1 rule", biggest news first."""
    parts = [f"{c[k]} {w}{'' if c[k] == 1 else 's'}" for k, w in
             (("facts", "fact"), ("events", "event"), ("rules", "rule"), ("chats", "past chat")) if c.get(k)]
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_status_line_ghost_reads_what_the_server_recalled(live, tmp_path):
    """Claude Code: the prompt hook writes .memory/pulse.json from the real server's answer; the status line's ghost
    reads it. Then the server is gone and the ghost says so."""
    url, store = live
    project = tmp_path / "project"
    (project / ".memory").mkdir(parents=True)
    path = tmp_path / "abc.jsonl"
    path.write_text(encoding="utf-8", data="".join(json.dumps(e) + "\n" for e in [
        {"type": "user", "message": {"role": "user", "content": "I drive a Toyota Corolla, nothing fancy."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}}]))
    base = {"session_id": "abc", "transcript_path": str(path), "cwd": str(project)}
    env = {"CLAUDE_PROJECT_DIR": str(project), "SPOOKLING_GHOST": "half", "COLORTERM": "truecolor"}
    hook(("node", ".js"), "stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False}, url, **env)
    out = hook(("node", ".js"), "user-prompt-submit", {**base, "hook_event_name": "UserPromptSubmit",
                                                      "prompt": "What car do I drive?"}, url, **env)
    ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]

    def status_line():
        run = subprocess.run(["node", str(HOOKS / "status-line.js")], capture_output=True, text=True, encoding="utf-8",
                             input=json.dumps({"session_id": "abc", "cwd": str(project), "workspace": {"project_dir": str(project)}}),
                             env={**os.environ, **env}, timeout=30)
        return [line for line in plain(run.stdout) if line]

    lines = status_line()
    assert any(line.endswith(f"spookling active · {spoken(hooks.counts(ctx)).replace(' and ', ' · ').replace(', ', ' · ')} recalled")
               for line in lines), lines
    assert has(lines, f"≈{kilo(len(ctx))} of the 10k characters of room"), lines
    hook(("node", ".js"), "user-prompt-submit", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "hi"}, nobody(), **env)
    assert any(line.endswith("spookling degraded · server off") for line in status_line()), "the server did not answer"


@pytest.mark.parametrize("runner", RUNNERS)
def test_hook_scripts_do_nothing_inside_a_tagging_call(live, tmp_path, runner):
    """SPOOKLING_TAGGING, set for a tagging CLI that may fire these hooks, makes every script exit at once."""
    url, store = live
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "I drive a Toyota Corolla."}}) + "\n")
    payload = {"session_id": "t", "transcript_path": str(path), "prompt": "What car do I drive?"}
    for name in ("stop", "pre-compact", "user-prompt-submit"):
        out = hook(runner, name, payload, url, SPOOKLING_TAGGING="1")
        assert (out.returncode, out.stdout, out.stderr) == (0, "", "")
    assert store.mem.log.events == []


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize("name", ["user-prompt-submit", "pre-compact", "stop"])
@pytest.mark.parametrize("where", ["refused", "dropped"])
def test_hook_scripts_never_block_claude_when_the_server_is_down(name, runner, where):
    """10.255.255.1 is not routed, so the connect hangs as a dead port on WSL's mirrored network does."""
    url = nobody() if where == "refused" else "http://10.255.255.1:7447"
    t = time.monotonic()
    out = hook(runner, name, {"session_id": "x", "prompt": "hi", "transcript_path": "/nope.jsonl"}, url)
    assert (out.returncode, out.stdout) == (0, "") and time.monotonic() - t < 5


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals; Windows stops the server with taskkill /F, see cli/README.md")
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
def test_serve_cleans_up_on_a_signal(tmp_path, sig):
    """uvicorn re-raises SIGTERM after shutdown: only the lifespan hook can close the store and its temp LOG."""
    import signal
    import sys
    import urllib.request

    tmp, port = tmp_path / "tmp", int(nobody().rsplit(":", 1)[1])
    tmp.mkdir()
    proc = subprocess.Popen([sys.executable, "-c", "from spookling_memory import main; main()", "serve", "--folder",
                             str(tmp_path / "mem"), "--port", str(port)], env={**os.environ, "TMPDIR": str(tmp)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/stats", timeout=1).read()
                break
            except OSError:
                time.sleep(0.5)
        assert list(tmp.glob("spookling-log-*"))  # the running server holds its temp copy
        proc.send_signal(getattr(signal, sig))
        proc.wait(30)
    finally:
        proc.kill()
    assert not list(tmp.glob("spookling-log-*"))


def own_server(tmp_path, name):
    """A project folder wired as init wires it: .memory/spookling.json naming the server, a uv, and no tagger. The uv
    is a stand-in that runs this test's Python, so the hooks start a real server without uv or a network."""
    import sys

    uv = tmp_path / "uv"
    if not uv.exists():
        uv.write_text(f'#!/bin/sh\nshift 4\nexec "{sys.executable}" -c "from spookling_memory import main; main()" "$@"\n')
        uv.chmod(0o755)
    mem = tmp_path / name / ".memory"
    mem.mkdir(parents=True)
    (mem / "spookling.json").write_text(json.dumps({"server": str(HOOKS.parent / "server"), "uv": str(uv), "model": "none"}))
    return mem.resolve()


def claude_hook(name, mem, payload):
    """A hook as init wires it: --memory names the folder, and no SPOOKLING_MEMORY_URL, so the hook finds or starts
    that folder's own server."""
    env = {k: v for k, v in os.environ.items() if k != "SPOOKLING_MEMORY_URL"}
    return subprocess.run(["node", str(HOOKS / f"{name}.js"), "--memory", str(mem)], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)


def stop_server(mem):
    import signal

    pid = json.loads((mem / "server.json").read_text())["pid"]
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not (mem / "server.json").exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"pid {pid} did not stop")


@pytest.mark.skipif(os.name == "nt" or not shutil.which("node"), reason="a POSIX stand-in for uv")
def test_hooks_start_each_projects_own_server_keep_projects_apart_and_restart_it(tmp_path):
    a, b = own_server(tmp_path, "shop"), own_server(tmp_path, "blog")
    try:
        start = claude_hook("session-start", a, {"session_id": "s1", "cwd": str(a.parent)})
        assert start.returncode == 0 and start.stdout == ""  # SessionStart output would become context
        port_a = json.loads((a / "server.json").read_text())["port"]
        p = json.loads((a / "pulse.json").read_text())
        assert p["event"] == "start" and p["up"] and p["remembered"] == 0  # the server's own count, not a guess

        transcript = tmp_path / "shop.jsonl"
        transcript.write_text("".join(json.dumps({"type": t["role"], "message": t}) + "\n" for t in turns(
            ["From now on, always use pnpm in this repo, never npm.", "Add a dark mode toggle to settings."])))
        assert claude_hook("stop", a, {"session_id": "s1", "transcript_path": str(transcript), "hook_event_name": "Stop",
                                       "cwd": str(a.parent)}).returncode == 0
        for _ in range(50):  # Stop replies at once and saves after
            if "pnpm" in (a / "log.jsonl").read_text() if (a / "log.jsonl").exists() else False:
                break
            time.sleep(0.1)
        ask = {"session_id": "s2", "prompt": "install lodash for the settings page", "hook_event_name": "UserPromptSubmit"}
        ctx = json.loads(claude_hook("user-prompt-submit", a, ask).stdout)["hookSpecificOutput"]["additionalContext"]
        assert "always use pnpm in this repo, never npm" in ctx.split("</rules>")[0]  # a standing rule with no tagger

        out = claude_hook("user-prompt-submit", b, ask)  # the blog's hook starts the blog's server
        port_b = json.loads((b / "server.json").read_text())["port"]
        assert port_b != port_a and "pnpm" not in out.stdout  # another project's memory never comes through

        stop_server(a)  # a crash, a reboot: the next hook brings it back with everything it had
        again = json.loads(claude_hook("user-prompt-submit", a, ask).stdout)["hookSpecificOutput"]["additionalContext"]
        assert "always use pnpm" in again and (a / "server.json").exists()
    finally:
        for m in (a, b):
            if (m / "server.json").exists():
                stop_server(m)


@pytest.mark.skipif(os.name == "nt" or not shutil.which("node"), reason="a POSIX stand-in for uv")
def test_codexs_first_hook_starts_the_server_and_its_ghost_says_what_it_holds(tmp_path):
    mem = own_server(tmp_path, "app")
    env = {k: v for k, v in os.environ.items() if k != "SPOOKLING_MEMORY_URL"}
    try:
        r = subprocess.run(["node", str(HOOKS / "spookling-codex.cjs"), "--memory", str(mem)], text=True, encoding="utf-8",
                           input=json.dumps({"hook_event_name": "SessionStart", "session_id": "c1", "cwd": str(mem.parent)}),
                           capture_output=True, env={**env, "NO_COLOR": "1"}, timeout=60)
        said = json.loads(r.stdout)["systemMessage"]
        assert said.startswith("spookling ready · local server connected") and "0 messages remembered" in said
        assert (mem / "server.json").exists()
    finally:
        if (mem / "server.json").exists():
            stop_server(mem)
