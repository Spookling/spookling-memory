"""The local HTTP API (FastAPI), bound to 127.0.0.1 only.

Two guards, because a web page in the user's browser can reach 127.0.0.1 too: the Host header must be 127.0.0.1 or
localhost (DNS rebinding), and a POST must say application/json (application/octet-stream for /v1/import), which a
page can only send after a CORS preflight this server never grants. No CORS is configured.
"""
from __future__ import annotations

import io
import threading
from contextlib import asynccontextmanager
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
POST_TYPES = ("application/json", "application/octet-stream")
WAIT = 10  # seconds a request waits for the store before a 503: under the hooks' 15 s, so they hear back


class TurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SessionIn(BaseModel):
    id: str
    ts: str | None = None  # ISO 8601; default now
    turns: list[TurnIn]
    agent: str = "user"
    tail: bool = False  # turns is the whole conversation so far: log only what is new (Store.ingest)


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

    @app.middleware("http")
    async def no_simple_posts(request: Request, call_next):
        if request.method == "POST" and request.headers.get("content-type", "").split(";")[0].strip() not in POST_TYPES:
            return JSONResponse({"detail": "send content-type application/json (application/octet-stream for /v1/import)"}, 415)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def refused(request: Request, e: ValueError):
        if isinstance(e, PydanticSerializationError):  # a ValueError too, but our bug, not a refusal: a 500, never a 409
            raise e
        return JSONResponse({"detail": str(e)}, 409)

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, e: RuntimeError):  # the writer model (no key, provider down), or Busy
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

    said: set[str] = set()  # each kind of refused hook body is logged once

    @app.post("/v1/hook/{event}")
    async def hook(event: str, request: Request, tasks: BackgroundTasks):
        """The body is read here, not declared as `payload: dict`: FastAPI answered 422 to an empty or unreadable body
        and nothing said why. Now an empty body is {} and only a body that is not one JSON object is refused (400)."""
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
        if event == "stop":  # reply at once; the tagging call runs after the response, so Claude is not held
            tasks.add_task(quietly, store, event, payload)
            return {}
        return await run_in_threadpool(hooks.handle, store, event, payload)

    return app


def quietly(store, event: str, payload: dict) -> None:
    """The Stop hook's work after its reply: an error can no longer reach Claude, so it is one log line. The turns
    stay in the transcript, and the next Stop of the session logs them."""
    try:
        hooks.handle(store, event, payload)
    except Exception as e:  # noqa: BLE001
        hooks.LOG.warning("hook %s: the turns were not logged this time (%s: %s)", event, type(e).__name__, e)


def serve(folder: str, port: int = PORT, **adapter) -> None:
    """Listens at once; the folder opens in the background (503 with Retry-After until it is open). A failed open
    stops the server with the reason, as before."""
    import uvicorn

    store = Store(folder, background=True, wait=WAIT, **adapter)
    app = create_app(store)

    @asynccontextmanager
    async def lifespan(_):
        yield
        # Here, not only in the finally: after a graceful shutdown on SIGINT or SIGTERM, uvicorn re-raises the signal,
        # which ends the process before a finally runs. close() removes the adapter's temp copy of the LOG.
        store.close()

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port))

    def opened():
        store.ready.wait()
        if store.failed is not None:
            hooks.LOG.error("%s could not be opened: %r", folder, store.failed)
            server.should_exit = True

    threading.Thread(target=opened, daemon=True).start()
    try:
        server.run()
    finally:
        store.close()  # idempotent; covers a failed start
    if store.failed is not None:
        raise store.failed
