"""Export and import: one archive of the two records; importing it anywhere gives the same answers."""
import io
import json
import tarfile

import pytest
from conftest import AFTER, NOW, QUESTIONS, RESET_AT, TAGS, Stub, fill

from spookling_memory.portability import import_archive, read_archive


@pytest.mark.parametrize("with_reset", [False, True])
def test_import_gives_the_same_context_for_the_same_question(open_store, tmp_path, with_reset):
    s = open_store()
    fill(s)
    s.compact("s3")
    if with_reset:
        s.reset("fresh start", RESET_AT)
        fill(s, [AFTER])
    before = [s.retrieve(q, NOW) for q in QUESTIONS]
    manifest = s.export(tmp_path / "a.spookling")
    assert manifest["counts"] == {"log": len(s.mem.log.events), "judgements": len(s.mem.log.decisions)}
    t = import_archive(tmp_path / "a.spookling", tmp_path / "elsewhere", triage=Stub(TAGS))
    try:
        assert t.mem.triage.judged == []
        assert [t.retrieve(q, NOW) for q in QUESTIONS] == before
        assert (tmp_path / "elsewhere" / "log.jsonl").read_text() == (tmp_path / "m" / "log.jsonl").read_text()
        assert (tmp_path / "elsewhere" / "judgements.jsonl").read_text() == (tmp_path / "m" / "judgements.jsonl").read_text()
    finally:
        t.close()


def test_import_refuses_a_memory_that_is_not_empty(open_store, tmp_path):
    s = open_store()
    fill(s)
    s.export(tmp_path / "a.spookling")
    s.close()
    log = (tmp_path / "m" / "log.jsonl").read_text()
    with pytest.raises(ValueError, match="not empty"):
        import_archive(tmp_path / "a.spookling", tmp_path / "m", triage=Stub(TAGS))
    assert (tmp_path / "m" / "log.jsonl").read_text() == log


def repack(src, out, change):
    with tarfile.open(src) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    change(files)
    with tarfile.open(out, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_a_damaged_or_foreign_archive_is_refused(open_store, tmp_path):
    s = open_store()
    fill(s)
    s.export(tmp_path / "a.spookling")
    with pytest.raises(ValueError, match="not a readable"):
        read_archive(io.BytesIO(b"not a tar"))
    repack(tmp_path / "a.spookling", tmp_path / "short.spookling",
           lambda f: f.update({"log.jsonl": b"".join(f["log.jsonl"].splitlines(keepends=True)[:-1])}))
    with pytest.raises(ValueError, match="incomplete"):
        read_archive(tmp_path / "short.spookling")
    repack(tmp_path / "a.spookling", tmp_path / "extra.spookling", lambda f: f.update({"../evil.sh": b"echo hi"}))
    with pytest.raises(ValueError, match="expected exactly"):
        read_archive(tmp_path / "extra.spookling")
    repack(tmp_path / "a.spookling", tmp_path / "other.spookling", lambda f: f.update(
        {"manifest.json": json.dumps({**json.loads(f["manifest.json"]), "version": 99}).encode()}))
    with pytest.raises(ValueError, match="unknown archive"):
        read_archive(tmp_path / "other.spookling")
    assert not (tmp_path.parent / "evil.sh").exists()
