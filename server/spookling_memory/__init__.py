"""spookling-memory serve | mcp | export | import: the local memory server over the algorithm in core/."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

CONFIG = "spookling.json"  # in the memory folder, written by `npx spookling init`: the tagger, and where the server is


def settings(folder: str) -> dict:
    """The folder's keys.env into the environment (a key already there wins) and its spookling.json, so a server
    the hooks start on their own runs as the one init started."""
    f = Path(folder)
    try:
        for m in re.finditer(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['\"]?([^'\"\r\n]*?)['\"]?\s*$",
                             (f / "keys.env").read_text(encoding="utf-8"), re.M):
            if m[2]:
                os.environ.setdefault(m[1], m[2])
    except OSError:
        pass
    try:
        c = json.loads((f / CONFIG).read_text(encoding="utf-8"))
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="spookling-memory", description="Spookling Memory, the local memory server")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mcp", help="stdio MCP server; talks to `serve` at SPOOKLING_MEMORY_URL")
    for name, what in (("serve", "run the HTTP API on 127.0.0.1"), ("export", "write a .spookling archive"),
                       ("import", "restore a .spookling archive into an empty folder")):
        s = sub.add_parser(name, help=what)
        s.add_argument("--folder", default=".memory", help="the memory folder (default .memory)")
        if name == "serve":
            s.add_argument("--port", type=int, help="default: the folder's own port, written to <folder>/server.json")
            s.add_argument("--model", help="tagger for tagging and the slow path: an OpenRouter model id, codex for "
                                           "your Codex subscription, or none for no model (default: the folder's "
                                           "spookling.json, else the adapter's, Haiku 4.5)")
            s.add_argument("--decider", help="decision model for every gate (typesafe/jev-1.14 on OpenRouter): typed "
                                             "Choice and Noul questions, each gated on its probability (default: off)")
            s.add_argument("--decide-threshold", type=float, default=0.8, help="Noul probability a gate needs (default 0.8)")
        else:
            s.add_argument("file")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        from .api import serve as run
        from .subscription import adapter

        c = settings(a.folder)
        model, decider = a.model or c.get("model"), a.decider or (None if a.model else c.get("decider"))
        run(a.folder, a.port, **adapter(model, decider, a.decide_threshold))
    elif a.cmd == "mcp":
        from .mcp import main as run

        run()
    elif a.cmd == "export":
        from .store import Store

        store = Store(a.folder)
        try:
            print(json.dumps(store.export(a.file)))
        finally:
            store.close()
    else:
        from .portability import import_archive

        import_archive(a.file, a.folder).close()
        print(f"restored {a.file} into {a.folder}")
