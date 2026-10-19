"""The local HTTP API (FastAPI), bound to 127.0.0.1 only.

Two guards, because a web page in the user's browser can reach 127.0.0.1 too: the Host header must be 127.0.0.1 or
localhost (DNS rebinding), and a POST must say application/json (application/octet-stream for /v1/import), which a
page can only send after a CORS preflight this server never grants. No CORS is configured.
"""
from __future__ import annotations

import io
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import hooks
from .portability import read_archive
from .store import Store

HOST, PORT = "127.0.0.1", 7447
POST_TYPES = ("application/json", "application/octet-stream")


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
        return JSONResponse({"detail": str(e)}, 409)

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, e: RuntimeError):  # the writer model (no key, provider down): try again later
        return JSONResponse({"detail": str(e)}, 503)

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

    @app.post("/v1/hook/{event}")
    def hook(event: str, payload: dict, tasks: BackgroundTasks) -> dict:
        if event not in hooks.EVENTS:
            raise HTTPException(404, f"hook events: {', '.join(hooks.EVENTS)}")
        if event == "stop":  # reply at once; the tagging call runs after the response, so Claude is not held
            tasks.add_task(hooks.handle, store, event, payload)
            return {}
        return hooks.handle(store, event, payload)

    return app


def serve(folder: str, port: int = PORT, **adapter) -> None:
    import uvicorn

    store = Store(folder, **adapter)
    try:
        uvicorn.run(create_app(store), host=HOST, port=port)
    finally:
        store.close()
