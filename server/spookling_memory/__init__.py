"""spookling-memory serve | mcp | export | import: the local memory server over the algorithm in core/."""
from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="spookling-memory", description="Spookling Memory, the local memory server")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mcp", help="stdio MCP server; talks to `serve` at SPOOKLING_MEMORY_URL")
    for name, what in (("serve", "run the HTTP API on 127.0.0.1"), ("export", "write a .spookling archive"),
                       ("import", "restore a .spookling archive into an empty folder")):
        s = sub.add_parser(name, help=what)
        s.add_argument("--folder", default=".memory", help="the memory folder (default .memory)")
        if name == "serve":
            s.add_argument("--port", type=int, default=7447)
            s.add_argument("--model", help="tagger for tagging and the slow path: an OpenRouter model id, or codex for "
                                           "your Codex subscription (default: the adapter's, Haiku 4.5)")
            s.add_argument("--decider", help="decision model for every gate (typesafe/jev-1.14 on OpenRouter): typed "
                                             "Choice and Noul questions, each gated on its probability (default: off)")
            s.add_argument("--decide-threshold", type=float, default=0.8, help="Noul probability a gate needs (default 0.8)")
        else:
            s.add_argument("file")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        from .api import serve as run
        from .subscription import adapter

        run(a.folder, a.port, **adapter(a.model, a.decider, a.decide_threshold))
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
