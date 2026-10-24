"""Optional local models for search: an embedder and a reranker (the `vectors` extra), every result cached on disk."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-base"
QUERY_PREFIX = {"BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
                "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: "}
CACHE = Path.home() / ".cache" / "spookling-memory" / "vectors.sqlite"  # the server and memory-bench each set their own
_lock = threading.Lock()
_models: dict[tuple[str, str], object] = {}
_db: sqlite3.Connection | None = None
_db_pid = 0
_vec_mem: dict[str, bytes] = {}  # every embedding this process has loaded or computed
_score_mem: dict[str, float] = {}  # likewise
TIMING = {"embed_texts": 0, "embed_seconds": 0.0, "rerank_pairs": 0, "rerank_seconds": 0.0}


def available() -> str | None:
    """None when the local models can run, else why not."""
    if os.environ.get("SPOOKLING_LOCAL_MODELS", "1") == "0":
        return "SPOOKLING_LOCAL_MODELS=0"
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return "the vectors extra is not installed (uv sync --extra vectors)"
    return None


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _conn() -> sqlite3.Connection:
    """The one connection of this process (a child after os.fork opens its own)."""
    global _db, _db_pid
    if _db is None or _db_pid != os.getpid():
        _db_pid = os.getpid()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(CACHE, check_same_thread=False, isolation_level=None, timeout=60)
        _db.execute("pragma journal_mode=wal")
        _db.execute("pragma synchronous=normal")  # a cache: losing the last writes on a crash only costs a recompute
        _db.execute("create table if not exists vec (key text primary key, v blob not null)")
        _db.execute("create table if not exists score (key text primary key, s real not null)")
    return _db


def _key(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def _model(kind: str, name: str):
    if (kind, name) not in _models:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        dev = device()
        m = SentenceTransformer(name, device=dev) if kind == "embed" else CrossEncoder(name, device=dev, max_length=512)
        if dev == "cuda":
            m.half()
        _models[(kind, name)] = m
    return _models[(kind, name)]


def embed(texts: list[str], model: str = EMBED_MODEL, query: bool = False):
    """Unit-length float32 vectors, one row per text (cached by text)."""
    import time

    import numpy as np

    texts = [QUERY_PREFIX.get(model, "") + t for t in texts] if query else list(texts)
    keys = [_key("embed", model, t) for t in texts]
    with _lock:
        got = _vec_mem
        miss = sorted({k for k in keys if k not in got})
        if miss:
            db = _conn()
            for i in range(0, len(miss), 500):
                chunk = miss[i:i + 500]
                got.update(db.execute(f"select key, v from vec where key in ({','.join('?' * len(chunk))})", chunk).fetchall())
        todo = sorted({i for i, k in enumerate(keys) if k not in got}, key=lambda i: len(texts[i]))
        if todo:
            t0 = time.monotonic()
            vecs = _model("embed", model).encode([texts[i] for i in todo], batch_size=64, normalize_embeddings=True,
                                                  convert_to_numpy=True, show_progress_bar=False)
            TIMING["embed_texts"] += len(todo)
            TIMING["embed_seconds"] += time.monotonic() - t0
            rows = [(keys[i], np.asarray(v, dtype=np.float16).tobytes()) for i, v in zip(todo, vecs, strict=True)]
            db = _conn()
            db.execute("begin")  # one short transaction, not one fsync per row
            db.executemany("insert or ignore into vec (key, v) values (?, ?)", rows)
            db.execute("commit")
            got.update(rows)
    return np.stack([np.frombuffer(got[k], dtype=np.float16).astype(np.float32) for k in keys]) if keys else np.zeros((0, 384), np.float32)


def rerank(query: str, texts: list[str], model: str = RERANK_MODEL) -> list[float]:
    """Cross-encoder relevance of each text to the query (higher is better; cached per pair)."""
    import time

    keys = [_key("rerank", model, query, t) for t in texts]
    with _lock:
        got = _score_mem
        miss = sorted({k for k in keys if k not in got})
        if miss:
            db = _conn()
            for i in range(0, len(miss), 500):
                chunk = miss[i:i + 500]
                got.update(db.execute(f"select key, s from score where key in ({','.join('?' * len(chunk))})", chunk).fetchall())
        todo = [i for i, k in enumerate(keys) if k not in got]
        if todo:
            t0 = time.monotonic()
            scores = _model("rerank", model).predict([(query, texts[i]) for i in todo], batch_size=32, show_progress_bar=False)
            TIMING["rerank_pairs"] += len(todo)
            TIMING["rerank_seconds"] += time.monotonic() - t0
            rows = [(keys[i], float(s)) for i, s in zip(todo, scores, strict=True)]
            db = _conn()
            db.execute("begin")
            db.executemany("insert or ignore into score (key, s) values (?, ?)", rows)
            db.execute("commit")
            got.update(rows)
    return [got[k] for k in keys]
