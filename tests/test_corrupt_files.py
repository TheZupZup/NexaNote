"""
A single unreadable or malformed file in the data directory (hand edits in
Obsidian, a half-copied file on the NAS, a non-UTF-8 export) must not take
down the listings the app and the sync depend on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.storage import FileNoteStore, PlainMarkdownNoteStore


@pytest.fixture(params=[FileNoteStore, PlainMarkdownNoteStore])
def api(request, tmp_path):
    db = request.param(tmp_path / "data")
    with TestClient(create_app(db), raise_server_exceptions=False) as c:
        yield c, db
    db.close()


@pytest.fixture
def file_api(tmp_path):
    """Frontmatter store: metadata lives in the .md file itself."""
    db = FileNoteStore(tmp_path / "data")
    with TestClient(create_app(db), raise_server_exceptions=False) as c:
        yield c, db
    db.close()


def _assert_listings_still_work(c, good_title="good"):
    for route in ("/notes", "/notebooks", "/stats", "/health"):
        assert c.get(route).status_code == 200, route
    assert good_title in [n["title"] for n in c.get("/notes").json()]


def test_invalid_utf8_note(api):
    c, db = api
    c.post("/notes", json={"title": "good"})
    (db.notes_dir / "latin1.md").write_bytes("caf\xe9 cr\xe8me".encode("latin-1"))
    _assert_listings_still_work(c)


@pytest.mark.parametrize("bad", [
    "note_type: bogus",
    "sync_status: weird",
    "updated_at: 'not a date'",
    "tags: 5",
])
def test_bad_frontmatter_value(file_api, bad):
    c, db = file_api
    c.post("/notes", json={"title": "good"})
    (db.notes_dir / "bad.md").write_text(
        f"---\nid: bad\ntitle: x\n{bad}\n---\nbody\n", encoding="utf-8"
    )
    _assert_listings_still_work(c)


def test_timestamp_without_timezone_is_read_as_utc(file_api):
    c, db = file_api
    c.post("/notes", json={"title": "good"})
    (db.notes_dir / "handedited.md").write_text(
        "---\nid: hand\ntitle: hand edited\nupdated_at: 2026-01-01T10:00:00\n---\nbody\n",
        encoding="utf-8",
    )
    _assert_listings_still_work(c)
    # Not skipped: a naive timestamp is valid data.
    assert "hand edited" in [n["title"] for n in c.get("/notes").json()]


def test_bad_notebook_file(api):
    c, db = api
    c.post("/notes", json={"title": "good"})
    c.post("/notebooks", json={"name": "ok"})
    (db.notebooks_dir / "x.yaml").write_bytes(b"id: x\nname: \xff\xfe\n")
    _assert_listings_still_work(c)
    assert [nb["name"] for nb in c.get("/notebooks").json()] == ["ok"]


def test_plain_sidecar_without_id(tmp_path):
    db = PlainMarkdownNoteStore(tmp_path / "data")
    with TestClient(create_app(db), raise_server_exceptions=False) as c:
        c.post("/notes", json={"title": "good"})
        (db.notes_dir / "Recipe.md").write_text("# Recipe\n", encoding="utf-8")
        (db.notes_dir / "Recipe.json").write_text('{"servings": 4}', encoding="utf-8")
        _assert_listings_still_work(c)
    db.close()


def test_corrupt_file_is_left_untouched(api):
    c, db = api
    c.post("/notes", json={"title": "good"})
    raw = "caf\xe9".encode("latin-1")
    path = db.notes_dir / "latin1.md"
    path.write_bytes(raw)
    c.get("/notes")
    c.get("/stats")
    assert path.read_bytes() == raw
