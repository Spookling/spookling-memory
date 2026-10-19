"""Spookling Memory: the local memory server over memory-bench's spookling adapter. See README.md.

    spookling-memory serve  [--folder .memory] [--port 8765] [--model M]   HTTP API on 127.0.0.1
    spookling-memory mcp                                                    stdio MCP tools (client of serve)
    spookling-memory export [--folder .memory] FILE                         the two records, one archive
    spookling-memory import [--folder .memory] FILE                         restore into an empty folder
"""
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
            s.add_argument("--port", type=int, default=8765)
            s.add_argument("--model", help="writer model for tagging and the slow path (default: the adapter's)")
        else:
            s.add_argument("file")
    a = p.parse_args(argv)
    adapter = {"model": a.model} if getattr(a, "model", None) else {}
    if a.cmd == "serve":
        from .api import serve as run

        run(a.folder, a.port, **adapter)
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
