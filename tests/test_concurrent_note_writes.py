"""
Concurrent REST writes to one note.

The app saves text and ink independently, so with a backend configured a
text autosave and an ink save for the same note can be in flight at once.
Each route reads the note, changes one part and saves it back; without
serialisation the slower one writes back a stale copy of the other's part.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.storage import FileNoteStore


@pytest.fixture
def api(tmp_path):
    db = FileNoteStore(tmp_path / "store")
    app = create_app(db)
    with TestClient(app) as c:
        yield c, db
    db.close()


def _slow_reads(db, delay=0.2):
    """Delay every note read so overlapping requests both read before
    either one writes, which is the window a slow disk or NAS opens."""
    real_get_note = db.get_note

    def get_note(*args, **kwargs):
        note = real_get_note(*args, **kwargs)
        time.sleep(delay)
        return note

    db.get_note = get_note
    return real_get_note


def _run_together(*requests):
    threads = [threading.Thread(target=r) for r in requests]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)


STROKE = {"id": "s1", "points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]}


def test_text_and_ink_saves_for_one_note_both_survive(api):
    c, db = api
    nid = c.post("/notes", json={"title": "T"}).json()["id"]
    real_get_note = _slow_reads(db)

    _run_together(
        lambda: c.put(f"/notes/{nid}/pages/1/text", json={"typed_content": "TEXT"}),
        lambda: c.put(f"/notes/{nid}/pages/1/ink", json={"strokes": [STROKE]}),
    )

    db.get_note = real_get_note
    page = c.get(f"/notes/{nid}/pages/1").json()
    assert page["typed_content"] == "TEXT"
    assert len(page["strokes"]) == 1


def test_rename_during_text_autosave_is_kept(api):
    c, db = api
    nid = c.post("/notes", json={"title": "Old title"}).json()["id"]
    real_get_note = _slow_reads(db)

    _run_together(
        lambda: c.put(f"/notes/{nid}/pages/1/text", json={"typed_content": "body"}),
        lambda: c.put(f"/notes/{nid}", json={"title": "New title"}),
    )

    db.get_note = real_get_note
    note = c.get(f"/notes/{nid}").json()
    assert note["title"] == "New title"
    assert note["pages"][0]["typed_content"] == "body"


def test_writes_to_different_notes_do_not_wait_for_each_other(api):
    c, db = api
    a = c.post("/notes", json={"title": "A"}).json()["id"]
    b = c.post("/notes", json={"title": "B"}).json()["id"]
    real_get_note = _slow_reads(db, delay=1.0)

    started = time.monotonic()
    _run_together(
        lambda: c.put(f"/notes/{a}/pages/1/text", json={"typed_content": "a"}),
        lambda: c.put(f"/notes/{b}/pages/1/text", json={"typed_content": "b"}),
    )
    elapsed = time.monotonic() - started

    db.get_note = real_get_note
    # One slow read per save: run one after the other they would take ~2s.
    assert elapsed < 1.8
