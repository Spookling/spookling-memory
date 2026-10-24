"""A `.spookling` archive (tar.gz): manifest.json, log.jsonl and judgements.jsonl; every view is rebuilt on import."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from .core import VERSION
from .store import JUDGEMENTS, LOG, Store, jsonl, now

FORMAT, ARCHIVE_VERSION = "spookling-memory", 1
MEMBERS = ("manifest.json", LOG, JUDGEMENTS)


def export(mem, path) -> dict:
    """path: a file path or a binary file object."""
    log, dec = mem.log.lines()
    manifest = {"format": FORMAT, "version": ARCHIVE_VERSION, "created": now(), "adapter": VERSION,
                "counts": {"log": len(log), "judgements": len(dec)}}
    files = {"manifest.json": json.dumps(manifest, indent=1) + "\n", LOG: "".join(x + "\n" for x in log),
             JUDGEMENTS: "".join(x + "\n" for x in dec)}
    with (tarfile.open(path, "w:gz") if isinstance(path, (str, Path)) else tarfile.open(fileobj=path, mode="w:gz")) as tar:
        for name in MEMBERS:
            data = files[name].encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o600
            tar.addfile(info, io.BytesIO(data))
    return manifest


def read_archive(path) -> tuple[list[dict], list[dict], dict]:
    """(events, judgements, manifest), read in memory and checked against the manifest."""
    try:
        with (tarfile.open(path, "r:gz") if isinstance(path, (str, Path)) else tarfile.open(fileobj=path, mode="r:gz")) as tar:
            members = {m.name: m for m in tar.getmembers()}
            if sorted(members) != sorted(MEMBERS) or not all(m.isfile() for m in members.values()):
                raise ValueError(f"expected exactly {', '.join(MEMBERS)}, found {', '.join(sorted(members)) or 'nothing'}")
            text = {n: tar.extractfile(members[n]).read().decode("utf-8") for n in MEMBERS}
        manifest = json.loads(text["manifest.json"])
        events = [json.loads(x) for x in jsonl(text[LOG]) if x.strip()]
        decisions = [json.loads(x) for x in jsonl(text[JUDGEMENTS]) if x.strip()]
    except (tarfile.TarError, OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a readable Spookling Memory archive: {e}") from None
    if manifest.get("format") != FORMAT or manifest.get("version") != ARCHIVE_VERSION:
        raise ValueError(f"unknown archive format {manifest.get('format')!r} version {manifest.get('version')!r}")
    if manifest.get("counts") != {"log": len(events), "judgements": len(decisions)}:
        raise ValueError(f"archive is incomplete: manifest says {manifest.get('counts')}, found {len(events)} and {len(decisions)} lines")
    return events, decisions, manifest


def import_archive(path, folder: str | Path, **adapter) -> Store:
    events, decisions, _ = read_archive(path)
    store = Store(folder, **adapter)
    try:
        store.restore(events, decisions)
    except BaseException:
        store.close()
        raise
    return store
