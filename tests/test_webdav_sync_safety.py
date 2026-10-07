"""
WebDAV sync engine against a real (in-process) WsgiDAV server: cases where
a sync used to lose or revert data.
"""

import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import requests
from cheroot import wsgi as cheroot_wsgi

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Point, SyncStatus
from nexanote.storage import FileNoteStore
from nexanote.sync.client import NexaNoteSyncEngine, SyncConfig
from nexanote.sync.server import build_app, ensure_default_notebook, ensure_storage_layout


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    db = FileNoteStore(tmp_path / "server_store")
    ensure_storage_layout(db)
    ensure_default_notebook(db)
    app = build_app(db, username="user", password="pass", verbose=False)
    port = _free_port()
    httpd = cheroot_wsgi.Server(bind_addr=("127.0.0.1", port), wsgi_app=app, numthreads=4)
    threading.Thread(target=httpd.start, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.options(url, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.05)
    yield {"url": url, "db": db}
    httpd.stop()


@pytest.fixture
def engine_for(server):
    def make(db):
        cfg = SyncConfig(
            server_url=server["url"], username="user", password="pass",
            timeout_seconds=5, backoff_seconds=(0.0,),
        )
        return NexaNoteSyncEngine(db, cfg)
    return make


def _server_text(server_db, note_id):
    note = server_db.get_note(note_id, load_pages=True)
    return note.pages[0].typed_content if note and note.pages else None


def _edit_text(db, note_id, text):
    """What PUT /notes/{id}/pages/1/text does."""
    note = db.get_note(note_id, load_pages=True)
    page = note.get_page(1)
    page.typed_content = text
    page.touch()
    note.touch()
    db.save_page(page)
    db.save_note(note, save_pages=False)


def _new_note(db, title="Doc", text="v1", **kwargs):
    note = Note(title=title, **kwargs)
    note.add_page().typed_content = text
    db.save_note(note)
    return note


def test_edit_of_synced_note_is_pushed(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    note = _new_note(db)
    assert engine_for(db).sync().success()

    _edit_text(db, note.id, "v2")
    report = engine_for(db).sync()

    assert report.success(), report.errors
    assert _server_text(server["db"], note.id) == "v2"
    assert db.get_note(note.id).sync_status == SyncStatus.SYNCED


def test_offline_text_edit_is_not_reverted_by_sync(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    note = _new_note(db)
    assert engine_for(db).sync().success()
    assert engine_for(db).sync().success()

    _edit_text(db, note.id, "my offline edit")
    assert engine_for(db).sync().success()

    assert db.get_note(note.id).pages[0].typed_content == "my offline edit"
    assert _server_text(server["db"], note.id) == "my offline edit"


def test_sync_round_trip_keeps_notebook_and_archived_flag(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    nb = Notebook(name="Work")
    db.save_notebook(nb)
    filed = _new_note(db, title="Meeting", notebook_id=nb.id)
    archived = _new_note(db, title="Old stuff")
    archived.is_archived = True
    db.save_note(archived)

    for _ in range(3):
        assert engine_for(db).sync().success()

    assert db.get_note(filed.id).notebook_id == nb.id
    assert db.get_note(archived.id).is_archived is True


def _fail_propfind(engine, status=503):
    real_request = engine.client.session.request

    def request(method, url, *args, **kwargs):
        if method == "PROPFIND":
            resp = requests.Response()
            resp.status_code = status
            resp._content = b"<html>maintenance</html>"
            return resp
        return real_request(method, url, *args, **kwargs)

    engine.client.session.request = request


@pytest.mark.parametrize("status", [401, 500, 503])
def test_failed_listing_aborts_instead_of_overwriting_remote(tmp_path, server, engine_for, status):
    a = FileNoteStore(tmp_path / "deviceA")
    b = FileNoteStore(tmp_path / "deviceB")
    note = _new_note(a)
    assert engine_for(a).sync().success()
    assert engine_for(b).sync().success()

    # B makes a newer edit and syncs it; A has an older, unsynced one.
    _edit_text(a, note.id, "A older edit")
    time.sleep(0.01)
    _edit_text(b, note.id, "B newer edit")
    assert engine_for(b).sync().success()

    engine = engine_for(a)
    _fail_propfind(engine, status)
    report = engine.sync()

    # Without a listing there was no pull to compare against, so nothing may
    # be pushed, and the sync must not claim success.
    assert not report.success()
    assert _server_text(server["db"], note.id) == "B newer edit"
    assert db_text(a, note.id) == "A older edit"


def db_text(db, note_id):
    return db.get_note(note_id).pages[0].typed_content


def test_failed_ink_download_keeps_local_strokes(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    note = Note(title="Sketch", note_type=NoteType.HANDWRITTEN)
    note.add_page().strokes.append(InkStroke(points=[Point(1, 1), Point(2, 2)]))
    db.save_note(note)
    assert engine_for(db).sync().success()

    engine = engine_for(db)
    real_get = engine.client.session.get

    def get(url, *args, **kwargs):
        if url.endswith(".ink"):
            raise requests.ConnectionError("dropped")
        return real_get(url, *args, **kwargs)

    engine.client.session.get = get
    report = engine.sync()

    assert not report.success()
    assert len(db.get_note(note.id).pages[0].strokes) == 1


def test_edit_made_during_upload_is_not_marked_synced(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    note = _new_note(db)
    engine = engine_for(db)
    real_put = engine.client.put_note_meta
    edited = []

    def put_then_edit(*args, **kwargs):
        result = real_put(*args, **kwargs)
        if not edited:
            edited.append(True)
            time.sleep(0.01)
            _edit_text(db, note.id, "typed while syncing")
        return result

    engine.client.put_note_meta = put_then_edit
    assert engine.sync().success()

    local = db.get_note(note.id)
    assert local.pages[0].typed_content == "typed while syncing"
    assert local.sync_status != SyncStatus.SYNCED

    assert engine_for(db).sync().success()
    assert _server_text(server["db"], note.id) == "typed while syncing"


def test_network_drop_after_ping_fails_fast(tmp_path, server, engine_for):
    db = FileNoteStore(tmp_path / "device")
    for i in range(10):
        _new_note(db, title=f"n{i}")
    engine = engine_for(db)
    real_request = engine.client.session.request
    calls = []

    def dead(method, url, *args, **kwargs):
        if method == "OPTIONS":
            return real_request(method, url, *args, **kwargs)
        calls.append(method)
        raise requests.ConnectTimeout("timeout")

    engine.client.session.request = dead
    engine.client.session.put = lambda url, *a, **kw: dead("PUT", url, *a, **kw)
    report = engine.sync()

    assert not report.success()
    # The root listing fails, so the sync stops there instead of retrying
    # the whole PROPFIND/MKCOL/PUT chain for every note.
    assert len(calls) <= 3


def test_put_with_a_foreign_id_does_not_delete_the_note_at_that_path(server):
    sdb = server["db"]
    nb = [n for n in sdb.list_notebooks() if n.id.startswith("00000000")][0]
    victim = Note(title="My Note", notebook_id=nb.id)
    page = victim.add_page()
    page.typed_content = "victim text"
    page.strokes.append(InkStroke(points=[Point(1, 1), Point(2, 2)]))
    sdb.save_note(victim)

    # "uncategorized/my-note" resolves to the victim through the title-slug
    # fallback; the body belongs to an unrelated note.
    body = {"id": "ffffffff-0000-0000-0000-000000000001", "title": "Other",
            "pages": [{"page_number": 1, "typed_content": "other"}]}
    resp = requests.put(server["url"] + "uncategorized/my-note/note.json",
                        json=body, auth=("user", "pass"), timeout=5)

    assert resp.status_code == 409
    kept = sdb.get_note(victim.id, load_pages=True)
    assert kept is not None
    assert kept.pages[0].typed_content == "victim text"
    assert len(kept.pages[0].strokes) == 1
