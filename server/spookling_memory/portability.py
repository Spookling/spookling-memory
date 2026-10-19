"""One archive per memory (a `.spookling` file, tar.gz): the two append-only records and a manifest.

    manifest.json     {"format": "spookling-memory", "version": 1, "created", "adapter", "counts": {"log", "judgements"}}
    log.jsonl         the LOG, one turn per line
    judgements.jsonl  the judgement log, one judgement or ruling per line

Every view (FACTS, EVENTS, INDEX, SEARCH) is rebuilt from these two on import, so the archive holds no view.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from bench.adapters.spookling import VERSION

from .store import JUDGEMENTS, LOG, Store, lines, now

FORMAT, ARCHIVE_VERSION = "spookling-memory", 1
MEMBERS = ("manifest.json", LOG, JUDGEMENTS)


def export(mem, path) -> dict:
    """Write `mem`'s two records to `path` (a file path, or a binary file object). Returns the manifest."""
    log, dec = lines(mem)
    manifest = {"format": FORMAT, "version": ARCHIVE_VERSION, "created": now(), "adapter": VERSION,
                "counts": {"log": len(log), "judgements": len(dec)}}
    files = {"manifest.json": json.dumps(manifest, indent=1) + "\n", LOG: "".join(x + "\n" for x in log),
             JUDGEMENTS: "".join(x + "\n" for x in dec)}
    with (tarfile.open(path, "w:gz") if isinstance(path, (str, Path)) else tarfile.open(fileobj=path, mode="w:gz")) as tar:
        for name in MEMBERS:
            data = files[name].encode()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o600
            tar.addfile(info, io.BytesIO(data))
    return manifest


def read_archive(path) -> tuple[list[dict], list[dict], dict]:
    """(log events, judgements, manifest) from an archive, checked: only the three members, read in memory (nothing
    is extracted to disk), the format and version known, and the line counts the manifest promises."""
    try:
        with (tarfile.open(path, "r:gz") if isinstance(path, (str, Path)) else tarfile.open(fileobj=path, mode="r:gz")) as tar:
            members = {m.name: m for m in tar.getmembers()}
            if sorted(members) != sorted(MEMBERS) or not all(m.isfile() for m in members.values()):
                raise ValueError(f"expected exactly {', '.join(MEMBERS)}, found {', '.join(sorted(members)) or 'nothing'}")
            text = {n: tar.extractfile(members[n]).read().decode() for n in MEMBERS}
        manifest = json.loads(text["manifest.json"])
        events = [json.loads(x) for x in text[LOG].splitlines()]
        decisions = [json.loads(x) for x in text[JUDGEMENTS].splitlines()]
    except (tarfile.TarError, OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a readable Spookling Memory archive: {e}") from None
    if manifest.get("format") != FORMAT or manifest.get("version") != ARCHIVE_VERSION:
        raise ValueError(f"unknown archive format {manifest.get('format')!r} version {manifest.get('version')!r}")
    if manifest.get("counts") != {"log": len(events), "judgements": len(decisions)}:
        raise ValueError(f"archive is incomplete: manifest says {manifest.get('counts')}, found {len(events)} and {len(decisions)} lines")
    return events, decisions, manifest


def import_archive(path, folder: str | Path, **adapter) -> Store:
    """Restore an archive into an empty memory folder and rebuild every view with settle(). Returns the open Store."""
    events, decisions, _ = read_archive(path)
    store = Store(folder, **adapter)
    try:
        store.restore(events, decisions)
    except BaseException:
        store.close()
        raise
    return store
