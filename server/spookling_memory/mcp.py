"""Stdio MCP tools: a thin client of `spookling-memory serve`, so the hooks and the tools share one memory."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.mcpserver import MCPServer

FOLDER = "x-spookling-folder"  # api.FOLDER; not imported, so the tools start without the server's dependencies

server = MCPServer("spookling-memory", instructions="Long-term memory for this user, kept on this machine. "
                   "memory_retrieve before answering from what the user said in past sessions.")
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # never send local memory through a proxy


LEGACY = "http://127.0.0.1:7447"  # a 0.1.x server: one fixed port, no server.json


def folder() -> Path | None:
    f = os.environ.get("SPOOKLING_MEMORY_FOLDER")
    return Path(f).resolve() if f else None


def base() -> str:
    """SPOOKLING_MEMORY_URL if set; else the port in SPOOKLING_MEMORY_FOLDER's server.json; else the old fixed port."""
    if os.environ.get("SPOOKLING_MEMORY_URL"):
        return os.environ["SPOOKLING_MEMORY_URL"]
    try:
        return f"http://127.0.0.1:{json.loads((folder() / 'server.json').read_text(encoding='utf-8'))['port']}"
    except (TypeError, OSError, ValueError, KeyError):
        return LEGACY


def alive(pid_file: Path) -> bool:
    try:
        os.kill(int(pid_file.read_text().strip()), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def ours(url: str, header: str | None) -> bool:
    """Whether the reply came from this folder's server: its x-spookling-folder names the folder. A 0.1.x server sends
    none; it counts only on the old fixed port with the folder's own server.pid alive. No folder set: any server."""
    mine = folder()
    if mine is None or os.environ.get("SPOOKLING_MEMORY_URL"):
        return True
    if header is None:
        return url == LEGACY and alive(mine / "server.pid")
    at = urllib.parse.unquote(header)
    return at.lower() == str(mine).lower() if os.name == "nt" else at == str(mine)


def call(path: str, body: dict | None = None, raw: bytes | None = None):
    url = base().rstrip("/") + path
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST", headers={
        "content-type": "application/octet-stream" if raw is not None else "application/json"})
    try:
        # A dead port on WSL's mirrored network is dropped, not refused: a 300 s read timeout would hang the tool there.
        u = urllib.parse.urlsplit(url)
        socket.create_connection((u.hostname, u.port or 80), timeout=2).close()
        with _open(req, timeout=300) as r:
            if not ours(base(), r.headers.get(FOLDER)):
                raise RuntimeError(f"the server at {url} serves another memory folder, not {folder()}; "
                                   "a hook or `npx spookling init` starts this folder's own")
            out = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: {e.code} {e.read().decode(errors='replace')}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"no Spookling Memory server at {url} ({getattr(e, 'reason', e)}); run `spookling-memory serve`") from None
    return out if path == "/v1/export" else json.loads(out)


@server.tool()
def memory_retrieve(question: str) -> dict:
    """What memory holds for a question: {context, digest, strong}. context is the block to read before answering."""
    return call("/v1/retrieve", {"question": question})


@server.tool()
def memory_ingest(session_id: str, turns: list[dict], ts: str | None = None) -> dict:
    """Log one session: turns is [{"role": "user" | "assistant", "content": "..."}], ts ISO 8601 (default now)."""
    return call("/v1/ingest", {"id": session_id, "turns": turns, "ts": ts})


@server.tool()
def memory_compact(session_id: str) -> dict:
    """Before a session is summarised: keep its rules, decisions, values, dates, paths and names verbatim.
    Returns the survivor block that leads every later context."""
    return call("/v1/compact", {"session_id": session_id})


@server.tool()
def memory_reset(note: str = "", undo: bool = False) -> dict:
    """Start fresh from now: facts in force are closed, earlier turns are shown only for questions about before the
    reset, earlier rules are no longer pinned. Nothing is deleted. undo=true reverses the latest reset."""
    return call("/v1/reset/undo", {}) if undo else call("/v1/reset", {"note": note})


@server.tool()
def memory_export(path: str) -> str:
    """Write the whole memory (its two records) to one archive file at path. Returns the path."""
    Path(path).expanduser().write_bytes(call("/v1/export"))
    return str(Path(path).expanduser())


@server.tool()
def memory_import(path: str) -> dict:
    """Restore an archive written by memory_export into this (empty) memory and rebuild every view."""
    return call("/v1/import", raw=Path(path).expanduser().read_bytes())


@server.tool()
def memory_stats() -> dict:
    """Counts and sizes of the stores, and the active reset if any."""
    return call("/v1/stats")


def main() -> None:
    server.run("stdio")
