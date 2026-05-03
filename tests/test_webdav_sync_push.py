"""
NexaNote — End-to-end tests for WebDAV sync push with file-based storage.

EN: These tests spin up a real WsgiDAV WSGI app backed by ``FileNoteStore``
    and exercise the same client code path used during sync. They cover
    the bug where PUT to a nested path (note metadata or page ink) returned
    409 because the WebDAV provider didn't auto-create parent collections,
    and where a writer crash bubbled up as 500.
FR: Tests bout-en-bout pour le push WebDAV avec le stockage fichier.
    Reproduit le bug 409 (parents manquants) + 500 (writer non-géré) et
    vérifie que les correctifs tiennent.
"""

import sys
import threading
import time
from pathlib import Path

import pytest
import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, str(Path(__file__).parent.parent))

from cheroot import wsgi as cheroot_wsgi

from nexanote.models.note import Note, Notebook, NoteType, SyncStatus
from nexanote.storage import FileNoteStore
from nexanote.sync.client import (
    DEFAULT_NOTEBOOK_SLUG,
    NexaNoteSyncEngine,
    SyncConfig,
    WebDAVClient,
)
from nexanote.sync.server import (
    DEFAULT_NOTEBOOK_ID_PREFIX,
    build_app,
    ensure_default_notebook,
    ensure_storage_layout,
    seed_demo_data,
)
from nexanote.sync.webdav_provider import (
    NexaNoteDAVProvider,
    NoteCollection,
    NotebookCollection,
    _id_with_prefix,
    _parse_slug,
    _slugify,
)


# ---------------------------------------------------------------------------
# Live server fixture — starts a real WebDAV server on a random port
# ---------------------------------------------------------------------------


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    """Run a real WsgiDAV server on a background thread."""
    db = FileNoteStore(tmp_path / "server_store")
    ensure_storage_layout(db)
    ensure_default_notebook(db)

    app = build_app(db, username="user", password="pass", verbose=False)
    port = _free_port()
    server = cheroot_wsgi.Server(
        bind_addr=("127.0.0.1", port),
        wsgi_app=app,
        numthreads=4,
        request_queue_size=8,
    )

    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    # Wait for the server to start accepting connections.
    base_url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.options(base_url, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        server.stop()
        raise RuntimeError("test WebDAV server failed to start")

    yield {
        "url": base_url,
        "username": "user",
        "password": "pass",
        "db": db,
    }

    server.stop()
    db.close()


def _make_client(live_server) -> WebDAVClient:
    return WebDAVClient(
        SyncConfig(
            server_url=live_server["url"],
            username=live_server["username"],
            password=live_server["password"],
            timeout_seconds=5,
        )
    )


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


class TestSlugHelpers:
    def test_parse_slug_extracts_id_prefix(self):
        title, prefix = _parse_slug("ma-note__abcd1234")
        assert prefix == "abcd1234"
        assert title.lower() == "ma note"

    def test_parse_slug_no_prefix(self):
        title, prefix = _parse_slug("ma-note")
        assert prefix is None
        assert title.lower() == "ma note"

    def test_parse_slug_rejects_non_hex_prefix(self):
        # Slugs from the client are always lowercase hex; reject anything else.
        title, prefix = _parse_slug("ma-note__ZZZZ1234")
        assert prefix is None  # non-hex → not a valid id prefix

    def test_id_with_prefix_preserves_first_8_chars(self):
        new_id = _id_with_prefix("abcd1234")
        assert new_id[:8] == "abcd1234"
        assert new_id.count("-") == 4  # canonical UUID layout

    def test_id_with_prefix_handles_none(self):
        new_id = _id_with_prefix(None)
        assert len(new_id) == 36
        assert new_id.count("-") == 4


# ---------------------------------------------------------------------------
# Provider unit tests — MKCOL & PUT into missing parents
# ---------------------------------------------------------------------------


def _make_environ(provider):
    return {
        "wsgidav.provider": provider,
        "wsgidav.config": {},
        "REQUEST_METHOD": "GET",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8765",
        "wsgi.url_scheme": "http",
    }


class TestProviderCreateCollection:
    def test_root_mkcol_creates_notebook_with_id_prefix(self, tmp_path):
        db = FileNoteStore(tmp_path / "store")
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)
        root = provider.get_resource_inst("/", environ)

        slug = "new-notebook__deadbeef"
        nb_collection = root.create_collection(slug)
        assert isinstance(nb_collection, NotebookCollection)
        assert nb_collection.notebook.id.startswith("deadbeef")

        # Subsequent path lookup must find the notebook at the same slug.
        again = provider.get_resource_inst(f"/{slug}", environ)
        assert again is not None
        assert isinstance(again, NotebookCollection)
        assert again.notebook.id == nb_collection.notebook.id

    def test_root_mkcol_is_idempotent(self, tmp_path):
        db = FileNoteStore(tmp_path / "store")
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)
        root = provider.get_resource_inst("/", environ)

        slug = "same-name__abcd1234"
        a = root.create_collection(slug)
        b = root.create_collection(slug)
        assert a.notebook.id == b.notebook.id

    def test_notebook_mkcol_creates_note_at_same_slug(self, tmp_path):
        db = FileNoteStore(tmp_path / "store")
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        nb_slug = _slugify(nb.name) + "__" + nb.id[:8]

        nb_col = provider.get_resource_inst(f"/{nb_slug}", environ)
        assert isinstance(nb_col, NotebookCollection)

        note_slug = "fresh-note__cafebabe"
        note_col = nb_col.create_collection(note_slug)
        assert isinstance(note_col, NoteCollection)
        assert note_col.note.id.startswith("cafebabe")

        # Path stays valid for the next request — no slug churn.
        resolved = provider.get_resource_inst(f"/{nb_slug}/{note_slug}", environ)
        assert resolved is not None
        assert isinstance(resolved, NoteCollection)
        assert resolved.note.id == note_col.note.id


class TestProviderCreateEmptyResource:
    """PUT into a missing file inside a note must succeed (auto-create)."""

    def test_create_empty_resource_for_new_page(self, tmp_path):
        db = FileNoteStore(tmp_path / "store")
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Note")
        note.add_page()
        db.save_note(note)
        full_note = db.get_note(note.id, load_pages=True)

        note_col = NoteCollection("/", environ, db, full_note)

        # page_2.ink doesn't exist yet — provider must allocate it.
        ink_file = note_col.create_empty_resource("page_2.ink")
        assert ink_file is not None

        reloaded = db.get_note(note.id, load_pages=True)
        assert any(p.page_number == 2 for p in reloaded.pages), (
            "create_empty_resource must materialize the new page on disk"
        )

    def test_create_empty_resource_rejects_unknown_filename(self, tmp_path):
        from wsgidav.dav_error import DAVError

        db = FileNoteStore(tmp_path / "store")
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Note")
        note.add_page()
        db.save_note(note)
        full_note = db.get_note(note.id, load_pages=True)

        note_col = NoteCollection("/", environ, db, full_note)

        with pytest.raises(DAVError):
            note_col.create_empty_resource("rogue.bin")


# ---------------------------------------------------------------------------
# Live server: PUT to missing parent must succeed (or be self-healing)
# ---------------------------------------------------------------------------


class TestLivePutIntoMissingParent:
    """
    EN: Reproduce the bug where the client did MKCOL → PUT and got 409
        because the parent never persisted. The server must now MKCOL
        nested paths transparently and the client retries on 409.
    """

    def test_mkcol_root_then_propfind_lists_notebook(self, live_server):
        url = live_server["url"]
        auth = HTTPBasicAuth(live_server["username"], live_server["password"])
        slug = "fresh-nb__a1b2c3d4"

        resp = requests.request("MKCOL", f"{url}{slug}", auth=auth, timeout=5)
        assert resp.status_code in (201, 405), resp.text

        # PROPFIND root and verify the new notebook shows up.
        resp = requests.request(
            "PROPFIND",
            url,
            auth=auth,
            headers={"Depth": "1"},
            timeout=5,
        )
        assert resp.status_code == 207
        assert slug in resp.text

    def test_put_note_json_after_mkcol_chain(self, live_server):
        """
        Full happy path: client MKCOL notebook + note, PUTs note.json,
        server stores it and a follow-up GET returns the same payload.
        """
        url = live_server["url"]
        auth = HTTPBasicAuth(live_server["username"], live_server["password"])

        nb_slug = "carnet__01234567"
        note_slug = "ma-note__89abcdef"

        # MKCOL chain
        for path in (nb_slug, f"{nb_slug}/{note_slug}"):
            resp = requests.request("MKCOL", f"{url}{path}", auth=auth, timeout=5)
            assert resp.status_code in (201, 405), f"MKCOL {path}: {resp.status_code}"

        # PUT note.json
        payload = {
            "id": "89abcdef-1234-5678-9abc-def012345678",
            "title": "Ma note synchronisée",
            "type": "typed",
            "tags": ["sync"],
            "is_pinned": False,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "pages": [
                {"page_number": 1, "template": "lined", "typed_content": "Hello"},
            ],
        }
        resp = requests.put(
            f"{url}{nb_slug}/{note_slug}/note.json",
            json=payload,
            auth=auth,
            timeout=5,
        )
        assert resp.status_code in (200, 201, 204), (
            f"PUT note.json failed: {resp.status_code} {resp.text}"
        )

        # GET back and ensure the payload round-tripped via the file store.
        resp = requests.get(
            f"{url}{nb_slug}/{note_slug}/note.json",
            auth=auth,
            timeout=5,
        )
        assert resp.status_code == 200
        roundtripped = resp.json()
        assert roundtripped["title"] == "Ma note synchronisée"
        assert roundtripped["pages"][0]["typed_content"] == "Hello"

    def test_put_into_missing_parent_returns_409_not_500(self, live_server):
        """
        EN: Direct PUT (without MKCOL) to a nested path with no parent must
            return 409, not 500 — and the body must hint that the parent
            collection is missing rather than leaking a stack trace.
        """
        url = live_server["url"]
        auth = HTTPBasicAuth(live_server["username"], live_server["password"])

        resp = requests.put(
            f"{url}does-not-exist__deadbeef/note__cafebabe/note.json",
            json={"id": "x", "pages": []},
            auth=auth,
            timeout=5,
        )
        # 409 (parent missing) is the WebDAV-correct answer — not 500.
        assert resp.status_code == 409, f"got {resp.status_code}: {resp.text}"


class TestSyncClientPushHappyPath:
    """End-to-end sync push using the high-level engine."""

    def _make_engine(self, live_server, client_dir):
        local_db = FileNoteStore(client_dir)
        engine = NexaNoteSyncEngine(
            local_db,
            SyncConfig(
                server_url=live_server["url"],
                username=live_server["username"],
                password=live_server["password"],
                timeout_seconds=5,
            ),
        )
        return local_db, engine

    def test_push_persists_notes_on_server(self, live_server, tmp_path):
        local_db, engine = self._make_engine(live_server, tmp_path / "client")

        nb = Notebook(name="Mon carnet", color="#3b82f6")
        local_db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Hello sync", note_type=NoteType.TYPED)
        page = note.add_page(template="lined")
        page.typed_content = "Synced via WebDAV"
        local_db.save_note(note)

        report = engine.sync()
        assert report.success(), f"sync failed: {report.errors}"
        assert report.notes_pushed == 1, report.summary()

        # Verify server side has the note.
        server_note = live_server["db"].get_note(note.id, load_pages=True)
        assert server_note is not None
        assert server_note.title == "Hello sync"
        assert server_note.pages[0].typed_content.strip().endswith("Synced via WebDAV")

    def test_push_recovers_from_409_via_mkcol_retry(self, live_server, tmp_path):
        """
        EN: Bypass the engine's pre-MKCOL and call ``put_note_meta`` directly
            against a path with no parent. The client must MKCOL the parents
            on the 409 and retry — the second attempt succeeds.
        """
        local_db = FileNoteStore(tmp_path / "client_only_dav")
        client = _make_client(live_server)

        nb_slug = "lazy__deadbeef"
        note_slug = "lazy-note__01020304"
        payload = {
            "id": "01020304-aaaa-bbbb-cccc-ddddeeeeffff",
            "title": "Lazy note",
            "type": "typed",
            "tags": [],
            "is_pinned": False,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "pages": [
                {"page_number": 1, "template": "blank", "typed_content": "lazy"},
            ],
        }
        ok, reason = client.put_note_meta(nb_slug, note_slug, payload)
        assert ok, f"expected MKCOL-on-409 retry to succeed: {reason}"

        # Server now has the note — the parent collections were auto-created.
        members = client.list_notebooks()
        assert any(m["name"] == nb_slug for m in members)

    def test_push_uncategorized_notebook_works(self, live_server, tmp_path):
        """A note with no notebook_id falls back to the "uncategorized" slug."""
        local_db, engine = self._make_engine(live_server, tmp_path / "client_uncat")

        note = Note(title="Loose note", note_type=NoteType.TYPED)
        note.add_page().typed_content = "no notebook"
        local_db.save_note(note)

        report = engine.sync()
        assert report.success(), f"sync failed: {report.errors}"
        # The fallback slug should now exist on the server.
        client = _make_client(live_server)
        names = {m["name"] for m in client.list_notebooks()}
        assert DEFAULT_NOTEBOOK_SLUG in names


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------


class TestServerBootstrap:
    def test_ensure_storage_layout_creates_dirs(self, tmp_path):
        db = FileNoteStore(tmp_path / "boot")
        # Wipe to simulate a clean slate after the constructor created them.
        for d in (db.notes_dir, db.drawings_dir, db.notebooks_dir):
            for child in d.iterdir():
                child.unlink()
            d.rmdir()
        ensure_storage_layout(db)
        assert db.notes_dir.is_dir()
        assert db.drawings_dir.is_dir()
        assert db.notebooks_dir.is_dir()

    def test_ensure_default_notebook_idempotent(self, tmp_path):
        db = FileNoteStore(tmp_path / "boot2")
        a = ensure_default_notebook(db)
        b = ensure_default_notebook(db)
        assert a.id == b.id
        assert a.id.startswith(DEFAULT_NOTEBOOK_ID_PREFIX)


# ---------------------------------------------------------------------------
# 500-mitigation: writer errors must surface as DAVError, not Internal Error
# ---------------------------------------------------------------------------


class TestWriterErrorReporting:
    def test_invalid_note_json_raises_dav_bad_request(self, tmp_path):
        from wsgidav.dav_error import DAVError

        db = FileNoteStore(tmp_path / "boot3")
        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Note")
        note.add_page()
        db.save_note(note)

        from nexanote.sync.webdav_provider import _NoteMetaWriter

        writer = _NoteMetaWriter(db, note)
        writer.write(b"not-json-at-all")
        with pytest.raises(DAVError):
            writer.close()

    def test_invalid_ink_payload_raises_dav_bad_request(self, tmp_path):
        from wsgidav.dav_error import DAVError

        db = FileNoteStore(tmp_path / "boot4")
        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Note")
        page = note.add_page()
        db.save_note(note)

        from nexanote.sync.webdav_provider import _InkWriter

        writer = _InkWriter(db, page, note)
        writer.write(b"{ this is not json")
        with pytest.raises(DAVError):
            writer.close()


# ---------------------------------------------------------------------------
# Welcome / demo note sync — the previously-failing 500 case
# ---------------------------------------------------------------------------


@pytest.fixture
def live_server_with_demo(tmp_path):
    """
    Live WebDAV server bootstrapped exactly like ``run_server`` first launch:
    fallback notebook + seeded demo content (notebook + welcome note).
    Mirrors the production path that produced the welcome-note 500 report.
    """
    db = FileNoteStore(tmp_path / "demo_server")
    ensure_storage_layout(db)
    ensure_default_notebook(db)
    seeded = seed_demo_data(db)
    assert seeded is not None, "seed_demo_data must produce a note on first run"

    app = build_app(db, username="user", password="pass", verbose=False)
    port = _free_port()
    server = cheroot_wsgi.Server(
        bind_addr=("127.0.0.1", port),
        wsgi_app=app,
        numthreads=4,
        request_queue_size=8,
    )
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.options(base_url, timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        server.stop()
        raise RuntimeError("test WebDAV server failed to start")

    yield {
        "url": base_url,
        "username": "user",
        "password": "pass",
        "db": db,
        "demo_note": seeded,
    }

    server.stop()
    db.close()


class TestDemoSeed:
    """
    Demo data must be created in a state that survives a full sync round-trip
    without ever round-tripping through a 500. The bug being fixed: a fresh
    server with seeded demo data triggered a partial-push failure on the
    welcome note when a sync engine ran against the same data dir.
    """

    def test_seed_demo_marks_data_synced(self, tmp_path):
        # Demo data must be SYNCED so a sync engine sharing the data dir
        # never tries to push it back to itself.
        db = FileNoteStore(tmp_path / "seed")
        ensure_default_notebook(db)
        note = seed_demo_data(db)
        assert note is not None
        assert note.sync_status == SyncStatus.SYNCED

        notebook = db.get_notebook(note.notebook_id)
        assert notebook is not None
        assert notebook.sync_status == SyncStatus.SYNCED

    def test_seed_demo_is_idempotent(self, tmp_path):
        db = FileNoteStore(tmp_path / "seed2")
        ensure_default_notebook(db)
        first = seed_demo_data(db)
        assert first is not None
        second = seed_demo_data(db)
        # Second call sees the user notebook and bails out — no duplication.
        assert second is None
        notebooks = [
            nb for nb in db.list_notebooks()
            if not nb.id.startswith(DEFAULT_NOTEBOOK_ID_PREFIX)
        ]
        assert len(notebooks) == 1


class TestWelcomeNoteSync:
    def test_self_sync_does_not_repush_demo_note(self, live_server_with_demo):
        """
        EN: When the same data dir backs both the WebDAV server and a sync
            engine (the layout used by ``python main.py``), the seeded
            welcome note must not be picked up for push. Previously its
            LOCAL_ONLY status caused the engine to PUT note.json + page_1.ink
            against itself, and any failure surfaced as a useless 500.
        """
        engine = NexaNoteSyncEngine(
            live_server_with_demo["db"],
            SyncConfig(
                server_url=live_server_with_demo["url"],
                username=live_server_with_demo["username"],
                password=live_server_with_demo["password"],
                timeout_seconds=5,
            ),
        )
        report = engine.sync()
        assert report.success(), f"self-sync should be a no-op: {report.errors}"
        assert report.notes_pushed == 0, (
            f"seeded demo data must stay put on self-sync, got {report.notes_pushed}"
        )

    def test_fresh_client_pulls_welcome_note_as_synced(
        self, live_server_with_demo, tmp_path
    ):
        """
        EN: A fresh client pulling from a server with seeded demo content
            must receive the welcome note locally, marked SYNCED. A second
            sync against an unchanged server must be a no-op — nothing to
            push, nothing to pull.
        """
        client_db = FileNoteStore(tmp_path / "client_welcome")
        engine = NexaNoteSyncEngine(
            client_db,
            SyncConfig(
                server_url=live_server_with_demo["url"],
                username=live_server_with_demo["username"],
                password=live_server_with_demo["password"],
                timeout_seconds=5,
            ),
        )

        # 1) Pull — welcome note arrives locally with SYNCED status.
        report = engine.sync()
        assert report.success(), f"pull failed: {report.errors}"
        assert report.notes_pulled == 1
        assert report.notes_pushed == 0

        demo_id = live_server_with_demo["demo_note"].id
        local = client_db.get_note(demo_id, load_pages=True)
        assert local is not None, "welcome note should be pulled to client"
        assert local.title == live_server_with_demo["demo_note"].title
        assert local.sync_status == SyncStatus.SYNCED
        assert local.pages and local.pages[0].typed_content

        # 2) Second sync — fully steady-state, no spurious push of the
        # demo note (which would have triggered the 500 in the bug report).
        steady = engine.sync()
        assert steady.success(), f"steady-state sync errors: {steady.errors}"
        assert steady.notes_pushed == 0

    def test_welcome_note_push_from_fresh_client_to_empty_server(
        self, live_server, tmp_path
    ):
        """
        EN: A separate scenario: the client locally creates a welcome note
            (e.g. on first launch) and pushes to a server that doesn't yet
            have one. Title/path must serialize and travel without a 500.
            Title is taken from the seeded demo so the test isn't pinned
            to a hardcoded literal.
        """
        seed_db = FileNoteStore(tmp_path / "seed_for_title")
        ensure_default_notebook(seed_db)
        seeded = seed_demo_data(seed_db)
        assert seeded is not None
        welcome_title = seeded.title

        client_db = FileNoteStore(tmp_path / "client_fresh_welcome")
        nb = Notebook(name="Mon premier carnet", color="#6366f1")
        client_db.save_notebook(nb)
        note = Note(
            notebook_id=nb.id,
            title=welcome_title,
            note_type=NoteType.TYPED,
        )
        page = note.add_page(template="lined")
        page.typed_content = (
            f"# {welcome_title}\n\nCette note a été créée automatiquement.\n"
        )
        client_db.save_note(note)

        engine = NexaNoteSyncEngine(
            client_db,
            SyncConfig(
                server_url=live_server["url"],
                username=live_server["username"],
                password=live_server["password"],
                timeout_seconds=5,
            ),
        )
        report = engine.sync()
        assert report.success(), f"welcome-note push failed: {report.errors}"
        assert report.notes_pushed == 1

        server_note = live_server["db"].get_note(note.id, load_pages=True)
        assert server_note is not None
        assert server_note.title == welcome_title


class TestPushSerializationFailures:
    """
    EN: When path generation or payload serialization blows up, the sync
        report must surface a useful reason — never a bare 500/traceback.
    """

    def test_push_reports_serialization_error_with_reason(self, tmp_path):
        from nexanote.sync.client import SyncReport

        db = FileNoteStore(tmp_path / "ser_fail")
        nb = Notebook(name="Carnet")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Bad note", note_type=NoteType.TYPED)
        note.add_page()
        db.save_note(note)

        engine = NexaNoteSyncEngine(
            db,
            SyncConfig(
                server_url="http://localhost:9999/",
                username="u",
                password="p",
            ),
        )

        # Stub path-bound network calls so the test never hits the wire,
        # then make `_serialize_note_meta` blow up to force the error path.
        engine.client.list_notebooks = lambda: []
        engine.client.list_notes = lambda nb_slug: []
        engine.client.create_notebook_dir = lambda nb_slug: True
        engine.client.create_note_dir = lambda nb_slug, note_slug: True
        engine.client.put_note_meta = lambda *a, **k: (True, None)
        engine.client.put_ink_page = lambda *a, **k: (True, None)

        from nexanote.sync import client as client_module

        original = client_module._serialize_note_meta
        try:
            def boom(_note):
                raise ValueError("simulated serialization bug")

            client_module._serialize_note_meta = boom
            report = SyncReport()
            engine._push_note(note, report)
        finally:
            client_module._serialize_note_meta = original

        assert report.errors, "serialization failure must produce a report error"
        msg = report.errors[0]
        assert "Bad note" in msg
        assert "serialization failed" in msg
        assert "simulated serialization bug" in msg


# ---------------------------------------------------------------------------
# ETag regression — WsgiDAV crashed on PUT when get_etag returned a quoted
# ISO timestamp ("\"2026-…\""). Lock down the contract: never quoted, never
# weak, always survives WsgiDAV's `checked_etag` validator.
# ---------------------------------------------------------------------------


class TestEtagFormat:
    """Hash-based ETags must satisfy WsgiDAV's `checked_etag`."""

    def _setup_note(self, tmp_path):
        db = FileNoteStore(tmp_path / "etag_store")
        nb = Notebook(name="Notebook")
        db.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Note", note_type=NoteType.TYPED)
        note.add_page()
        db.save_note(note)
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)
        return db, db.get_note(note.id, load_pages=True), environ

    def test_note_meta_etag_passes_wsgidav_checked_etag(self, tmp_path):
        from wsgidav.util import checked_etag

        from nexanote.sync.webdav_provider import NoteMetaFile

        db, note, environ = self._setup_note(tmp_path)
        meta = NoteMetaFile("/x/y/note.json", environ, db, note)
        etag = meta.get_etag()
        # WsgiDAV would otherwise raise ValueError on a quoted/weak token.
        assert checked_etag(etag) == etag

    def test_ink_etag_passes_wsgidav_checked_etag(self, tmp_path):
        from wsgidav.util import checked_etag

        from nexanote.sync.webdav_provider import InkFile

        db, note, environ = self._setup_note(tmp_path)
        page = note.pages[0]
        ink = InkFile("/x/y/page_1.ink", environ, db, page, note)
        etag = ink.get_etag()
        assert checked_etag(etag) == etag

    def test_etag_is_never_quoted(self, tmp_path):
        from nexanote.sync.webdav_provider import InkFile, NoteMetaFile

        db, note, environ = self._setup_note(tmp_path)
        meta_etag = NoteMetaFile("/p", environ, db, note).get_etag()
        ink_etag = InkFile("/p", environ, db, note.pages[0], note).get_etag()

        for etag in (meta_etag, ink_etag):
            assert etag, "etag must be a non-empty token"
            assert '"' not in etag, f"etag must not contain quotes: {etag!r}"
            assert not etag.startswith("'") and not etag.endswith("'"), etag
            assert not etag.startswith("W/"), "weak etags are rejected by WsgiDAV"
            # A quoted-then-stripped token would still leak surrounding
            # whitespace; hex digests can't.
            assert etag == etag.strip()

    def test_etag_changes_when_content_changes(self, tmp_path):
        from nexanote.sync.webdav_provider import NoteMetaFile

        db, note, environ = self._setup_note(tmp_path)
        before = NoteMetaFile("/p", environ, db, note).get_etag()
        # Force a measurable timestamp delta — `touch()` quantizes to ~µs but
        # consecutive calls can land in the same tick on fast machines.
        import time
        time.sleep(0.001)
        note.touch()
        after = NoteMetaFile("/p", environ, db, note).get_etag()
        assert before != after

    def test_safe_etag_helper_handles_mixed_inputs(self):
        from nexanote.sync.webdav_provider import _safe_etag

        from datetime import datetime, timezone

        now = datetime(2026, 5, 3, 8, 49, 9, 990410, tzinfo=timezone.utc)
        token = _safe_etag("note", "abc", now, 1)
        assert '"' not in token
        assert token == _safe_etag("note", "abc", now, 1)  # deterministic
        assert token != _safe_etag("note", "abc", now, 2)  # part-sensitive


class TestLivePutNoCrashOnEtag:
    """
    EN: Reproduce the exact crash from the bug report — PUT on note.json and
        page_1.ink against a note whose ETag previously rendered as
        ``""<iso>""``. WsgiDAV's `checked_etag` would raise ValueError → 500.
        With a hash-based ETag, both PUTs succeed end-to-end.
    """

    def _seed_note_via_mkcol(self, url, auth):
        nb_slug = "etag-nb__c0ffee01"
        note_slug = "etag-note__c0ffee02"
        for path in (nb_slug, f"{nb_slug}/{note_slug}"):
            resp = requests.request("MKCOL", f"{url}{path}", auth=auth, timeout=5)
            assert resp.status_code in (201, 405), resp.text
        return nb_slug, note_slug

    def test_put_note_json_does_not_crash_on_etag(self, live_server):
        url = live_server["url"]
        auth = HTTPBasicAuth(live_server["username"], live_server["password"])
        nb_slug, note_slug = self._seed_note_via_mkcol(url, auth)

        payload = {
            "id": "c0ffee02-1111-2222-3333-444455556666",
            "title": "Etag note",
            "type": "typed",
            "tags": [],
            "is_pinned": False,
            "created_at": "2026-05-03T08:49:09.990410+00:00",
            "updated_at": "2026-05-03T08:49:09.990410+00:00",
            "pages": [
                {"page_number": 1, "template": "blank", "typed_content": "hi"},
            ],
        }
        resp = requests.put(
            f"{url}{nb_slug}/{note_slug}/note.json",
            json=payload,
            auth=auth,
            timeout=5,
        )
        assert resp.status_code in (200, 201, 204), (
            f"PUT note.json must not 500 on etag — got {resp.status_code}: {resp.text}"
        )

        # Server is now expected to expose a strong, quote-free ETag.
        head = requests.request(
            "HEAD",
            f"{url}{nb_slug}/{note_slug}/note.json",
            auth=auth,
            timeout=5,
        )
        if "ETag" in head.headers:
            etag = head.headers["ETag"]
            # Header value: WsgiDAV wraps the token in exactly one pair of
            # quotes. Anything beyond that is double-quoting.
            assert etag.count('"') == 2, f"etag double-quoted in header: {etag!r}"
            assert not etag.startswith('""'), f"double-leading-quote: {etag!r}"
            assert not etag.endswith('""'), f"double-trailing-quote: {etag!r}"

    def test_put_page_1_ink_does_not_crash_on_etag(self, live_server):
        url = live_server["url"]
        auth = HTTPBasicAuth(live_server["username"], live_server["password"])
        nb_slug, note_slug = self._seed_note_via_mkcol(url, auth)

        # Materialise the note first so page_1.ink has a target.
        meta_payload = {
            "id": "c0ffee02-9999-aaaa-bbbb-cccccccccccc",
            "title": "Ink note",
            "type": "typed",
            "tags": [],
            "is_pinned": False,
            "created_at": "2026-05-03T08:49:09.990410+00:00",
            "updated_at": "2026-05-03T08:49:09.990410+00:00",
            "pages": [
                {"page_number": 1, "template": "blank", "typed_content": ""},
            ],
        }
        resp = requests.put(
            f"{url}{nb_slug}/{note_slug}/note.json",
            json=meta_payload,
            auth=auth,
            timeout=5,
        )
        assert resp.status_code in (200, 201, 204), resp.text

        ink_payload = {
            "page_id": "page-1",
            "note_id": meta_payload["id"],
            "page_number": 1,
            "template": "blank",
            "width_px": 800,
            "height_px": 1200,
            "updated_at": "2026-05-03T08:49:09.990410+00:00",
            "strokes": [],
        }
        resp = requests.put(
            f"{url}{nb_slug}/{note_slug}/page_1.ink",
            json=ink_payload,
            auth=auth,
            timeout=5,
        )
        assert resp.status_code in (200, 201, 204), (
            f"PUT page_1.ink must not 500 on etag — got {resp.status_code}: {resp.text}"
        )

        head = requests.request(
            "HEAD",
            f"{url}{nb_slug}/{note_slug}/page_1.ink",
            auth=auth,
            timeout=5,
        )
        if "ETag" in head.headers:
            etag = head.headers["ETag"]
            assert etag.count('"') == 2, f"etag double-quoted in header: {etag!r}"
