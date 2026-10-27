"""Local HTTP API. A browser page can reach 127.0.0.1 too, hence the Host check and JSON-only POSTs (no CORS)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_core import PydanticSerializationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import hooks
from .portability import read_archive
from .store import Busy, Store

HOST, PORT = "127.0.0.1", 7447
PORTS = (7448, 500)  # a folder's own port comes from this range (first, how many), so two projects never share one
SERVER_FILE = "server.json"  # in the folder: the port it listens on and its pid, for the hooks and the MCP tools
FOLDER = "x-spookling-folder"  # on every reply: which folder answered, so a hook never talks to another one's memory
POST_TYPES = ("application/json", "application/octet-stream")
WAIT = 10  # seconds before a 503: under the hooks' 15 s, so they hear back


class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionIn(BaseModel):
    id: str
    ts: str | None = None
    turns: list[TurnIn]
    agent: str = "user"
    tail: bool = False  # turns is the whole conversation so far


class QuestionIn(BaseModel):
    question: str
    ts: str | None = None


class SessionRef(BaseModel):
    session_id: str


class NoteIn(BaseModel):
    note: str = ""


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="Spookling Memory", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
    here = str(Path(store.folder).resolve())

    @app.middleware("http")
    async def which_folder(request: Request, call_next):
        response = await call_next(request)
        response.headers[FOLDER] = here
        return response

    @app.middleware("http")
    async def no_simple_posts(request: Request, call_next):
        if request.method == "POST" and request.headers.get("content-type", "").split(";")[0].strip() not in POST_TYPES:
            return JSONResponse({"detail": "send content-type application/json (application/octet-stream for /v1/import)"}, 415)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def refused(request: Request, e: ValueError):
        if isinstance(e, PydanticSerializationError):  # a ValueError too, but our bug: a 500, not a 409
            raise e
        return JSONResponse({"detail": str(e)}, 409)

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, e: RuntimeError):  # the writer model failed, or Busy
        busy = {"Retry-After": str(Busy.retry_after)} if isinstance(e, Busy) else None
        return JSONResponse({"detail": str(e)}, 503, headers=busy)

    @app.post("/v1/ingest")
    def ingest(s: SessionIn) -> dict:
        return {"logged": store.ingest(s.id, [t.model_dump() for t in s.turns], s.ts, s.agent, s.tail)}

    @app.post("/v1/retrieve")
    def retrieve(q: QuestionIn) -> dict:
        return store.retrieve(q.question, q.ts)

    @app.post("/v1/compact")
    def compact(s: SessionRef) -> dict:
        return store.compact(s.session_id)

    @app.post("/v1/reset")
    def reset(n: NoteIn) -> dict:
        return store.reset(n.note)

    @app.post("/v1/reset/undo")
    def undo() -> dict:
        return store.undo_reset()

    @app.get("/v1/export")
    def export() -> Response:
        buf = io.BytesIO()
        store.export(buf)
        return Response(buf.getvalue(), media_type="application/gzip",
                        headers={"content-disposition": 'attachment; filename="memory.spookling"'})

    @app.post("/v1/import")
    async def import_(request: Request) -> dict:
        events, decisions, _ = read_archive(io.BytesIO(await request.body()))
        return await run_in_threadpool(store.restore, events, decisions)

    @app.get("/v1/stats")
    def stats() -> dict:
        return store.stats()

    said: set[str] = set()

    @app.post("/v1/hook/{event}")
    async def hook(event: str, request: Request, tasks: BackgroundTasks):
        """Read by hand: as `payload: dict`, FastAPI answered an empty body 422 and said nothing."""
        if event not in hooks.EVENTS:
            raise HTTPException(404, f"hook events: {', '.join(hooks.EVENTS)}")
        body = await request.body()
        try:
            payload = hooks.payload(body)
        except ValueError as e:
            kind = str(e).split(" at character")[0]
            if (event, kind) not in said:
                said.add((event, kind))
                hooks.LOG.warning("hook %s: %d-byte body refused, logged once per kind: %s; it starts %r", event,
                                  len(body), e, body[:80])
            return JSONResponse({"detail": str(e)}, 400)
        if event == "stop":  # reply at once: the tagging runs after the response
            tasks.add_task(quietly, store, event, payload)
            return {}
        body, pulse = await run_in_threadpool(hooks.handle, store, event, payload)
        if store.tag_error:  # recall works, tagging does not: the status line says why
            pulse = {**pulse, "tagging": store.tag_error[:160]}
        return JSONResponse(body, headers={hooks.PULSE: json.dumps(pulse, separators=(",", ":"))} if pulse else None)

    return app


def quietly(store, event: str, payload: dict) -> None:
    """After the reply an error cannot reach Claude: log it; the next Stop logs the turns."""
    try:
        hooks.handle(store, event, payload)
    except Exception as e:  # noqa: BLE001
        hooks.LOG.warning("hook %s: the turns were not logged this time (%s: %s)", event, type(e).__name__, e)


def port_for(folder: str | Path) -> int:
    first, n = PORTS
    return first + int(hashlib.sha256(str(Path(folder).resolve()).encode()).hexdigest(), 16) % n


def bind(folder: str | Path, port: int | None) -> socket.socket:
    """The given port, else the folder's own and the next ones up (wrapping in PORTS) until one is free."""
    first, n = PORTS
    tries = [port] if port else [first + (port_for(folder) - first + i) % n for i in range(20)]
    for p in tries:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, p))
            return s
        except OSError:
            s.close()
            if port:
                raise
    raise OSError(f"no free port among {tries[0]}..{tries[-1]} on {HOST}")


def serve(folder: str, port: int | None = None, **adapter) -> None:
    """Listens at once; the folder opens in the background, 503 until then. port None: the folder's own port,
    written to <folder>/server.json once bound (and removed on the way out) for the hooks to find."""
    import uvicorn

    store = Store(folder, background=True, wait=WAIT, **adapter)  # the folder's lock first: one server per folder
    try:
        sock = bind(folder, port)
    except OSError:
        store.close()
        raise
    info = Path(folder) / SERVER_FILE
    mine = {"port": sock.getsockname()[1], "pid": os.getpid(), "folder": str(Path(folder).resolve())}
    info.write_text(json.dumps(mine) + "\n", encoding="utf-8")
    (Path(folder) / "server.starting").unlink(missing_ok=True)  # a hook's start lock: started, the next start may go
    app = create_app(store)

    def leave() -> None:
        store.close()
        try:  # only our own: a newer server for this folder may have written its port since
            if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass

    @asynccontextmanager
    async def lifespan(_):
        yield
        # here too: uvicorn re-raises SIGINT or SIGTERM after shutdown, which ends the process before a finally runs
        leave()

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=mine["port"]))

    def opened():
        store.ready.wait()
        if store.failed is not None:
            hooks.LOG.error("%s could not be opened: %r", folder, store.failed)
            server.should_exit = True

    threading.Thread(target=opened, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        leave()  # covers a failed start
    if store.failed is not None:
        raise store.failed
