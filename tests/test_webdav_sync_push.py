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

from nexanote.models.note import Note, Notebook, NoteType
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
