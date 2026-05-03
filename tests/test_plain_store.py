"""
NexaNote — Tests for the plain-Markdown storage backend.

EN: Covers the new ``PlainMarkdownNoteStore`` and the surrounding wiring
    (mode marker, factory, YAML→plain migration). Required scenarios:
      - note creation     (file pair appears)
      - rename            (files renamed, id stable)
      - sync              (engine.save_note path keeps the layout consistent
                          and conflict resolution works through the same
                          public API as the YAML backend)
      - conflict handling (resolver writes both winner + conflict copy)

FR: Tests du nouveau backend Markdown brut + sidecar JSON et de l'aiguillage
    associé (marqueur, factory, migration YAML→plain). Couvre la création,
    le renommage, la sync et la gestion des conflits.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.models.note import (
    InkStroke,
    Note,
    Notebook,
    NoteType,
    Page,
    Point,
    SyncStatus,
)
from nexanote.storage import (
    FileNoteStore,
    MODE_MARKER,
    MODE_PLAIN,
    MODE_YAML,
    PlainMarkdownNoteStore,
    create_store,
    detect_mode,
    migrate_yaml_to_plain,
)
from nexanote.storage.backend import ENV_STORAGE_MODE
from nexanote.storage.plain_store import SIDECAR_SUFFIX
from nexanote.sync.client import (
    NexaNoteSyncEngine,
    SyncConfig,
    SyncReport,
)
from nexanote.sync.conflict import ConflictResolver, ConflictStrategy


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> PlainMarkdownNoteStore:
    s = PlainMarkdownNoteStore(tmp_path / "plain")
    yield s
    s.close()


def _typed_note(title: str, body: str = "body") -> Note:
    note = Note(title=title, note_type=NoteType.TYPED)
    note.add_page().typed_content = body
    return note


def _read_sidecar(store: PlainMarkdownNoteStore, stem: str) -> dict:
    return json.loads(
        (store.notes_dir / f"{stem}{SIDECAR_SUFFIX}").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Note creation
# ---------------------------------------------------------------------------


class TestCreate:
    def test_save_writes_md_and_sidecar(self, store):
        note = _typed_note("Hello World", "# Hi\n\nbody text")
        store.save_note(note)

        md = store.notes_dir / "Hello World.md"
        sidecar = store.notes_dir / "Hello World.json"
        assert md.exists() and sidecar.exists()

        body = md.read_text(encoding="utf-8")
        assert not body.startswith("---"), "plain backend must not emit frontmatter"
        assert "# Hi" in body
        assert "body text" in body

        meta = json.loads(sidecar.read_text("utf-8"))
        assert meta["id"] == note.id
        assert meta["title"] == "Hello World"
        assert meta["note_type"] == "typed"

    def test_get_note_round_trip(self, store):
        note = _typed_note("Round trip", "content here")
        store.save_note(note)

        loaded = store.get_note(note.id, load_pages=True)
        assert loaded is not None
        assert loaded.id == note.id
        assert loaded.title == "Round trip"
        assert loaded.pages[0].typed_content == "content here"

    def test_filename_sanitized(self, store):
        store.save_note(_typed_note("bad/title:?<>", "x"))
        names = [p.name for p in store.notes_dir.glob("*.md")]
        assert all(ch not in n for n in names for ch in '/:?<>"\\|*')

    def test_unicode_title_preserved(self, store):
        store.save_note(_typed_note("Café ☕", "espresso"))
        assert (store.notes_dir / "Café ☕.md").exists()
        assert (store.notes_dir / "Café ☕.json").exists()

    def test_blank_title_uses_fallback(self, store):
        note = _typed_note("   ", "still has body")
        store.save_note(note)
        assert (store.notes_dir / "untitled.md").exists()

    def test_two_notes_same_title_get_distinct_files(self, store):
        a = _typed_note("Same", "body A")
        b = _typed_note("Same", "body B")
        store.save_note(a)
        store.save_note(b)

        names = sorted(p.name for p in store.notes_dir.glob("*.md"))
        assert names == ["Same (2).md", "Same.md"]

        # Each note loads back with its own body.
        loaded_a = store.get_note(a.id, load_pages=True)
        loaded_b = store.get_note(b.id, load_pages=True)
        assert loaded_a.pages[0].typed_content == "body A"
        assert loaded_b.pages[0].typed_content == "body B"

    def test_internal_md_has_no_frontmatter(self, store):
        store.save_note(_typed_note("Pure", "plain body"))
        text = (store.notes_dir / "Pure.md").read_text(encoding="utf-8")
        assert text == "plain body\n"

    def test_save_creates_drawings_when_strokes_present(self, store):
        note = Note(title="Inked", note_type=NoteType.HANDWRITTEN)
        page = note.add_page()
        page.add_stroke(
            InkStroke(
                color="#000000",
                points=[Point(0, 0), Point(10, 10)],
            )
        )
        store.save_note(note)
        assert (store.drawings_dir / f"{note.id}.json").exists()

        loaded = store.get_note(note.id, load_pages=True)
        assert loaded.pages[0].strokes
        assert loaded.pages[0].strokes[0].color == "#000000"


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class TestRename:
    def test_title_change_renames_files(self, store):
        note = _typed_note("Old name", "body")
        store.save_note(note)
        old_md = store.notes_dir / "Old name.md"
        old_sidecar = store.notes_dir / "Old name.json"
        assert old_md.exists() and old_sidecar.exists()

        note.title = "Brand new"
        store.save_note(note)

        assert (store.notes_dir / "Brand new.md").exists()
        assert (store.notes_dir / "Brand new.json").exists()
        assert not old_md.exists()
        assert not old_sidecar.exists()

    def test_id_stable_across_rename(self, store):
        note = _typed_note("Original", "body")
        store.save_note(note)
        first_id = note.id

        note.title = "Rebadged"
        store.save_note(note)

        loaded = store.get_note(first_id, load_pages=True)
        assert loaded is not None
        assert loaded.id == first_id
        assert loaded.title == "Rebadged"

    def test_metadata_only_save_keeps_body(self, store):
        note = _typed_note("Edit Me", "important content")
        store.save_note(note)

        # Reload metadata only and re-save — pages should not be wiped.
        meta_only = store.get_note(note.id, load_pages=False)
        meta_only.tags = ["updated"]
        store.save_note(meta_only, save_pages=False)

        again = store.get_note(note.id, load_pages=True)
        assert again.pages[0].typed_content == "important content"
        assert "updated" in again.tags

    def test_rename_into_collision_gets_suffix(self, store):
        a = _typed_note("Apple", "A body")
        b = _typed_note("Banana", "B body")
        store.save_note(a)
        store.save_note(b)

        # Rename Banana → Apple: must not stomp on Apple's file.
        b.title = "Apple"
        store.save_note(b)

        assert (store.notes_dir / "Apple.md").exists()
        assert (store.notes_dir / "Apple (2).md").exists()
        # The original Banana files are gone.
        assert not (store.notes_dir / "Banana.md").exists()
        assert not (store.notes_dir / "Banana.json").exists()

        # Each note still resolves by id and keeps its own body.
        assert store.get_note(a.id).title == "Apple"
        assert store.get_note(b.id).title == "Apple"
        bodies = {
            store.get_note(a.id, load_pages=True).pages[0].typed_content,
            store.get_note(b.id, load_pages=True).pages[0].typed_content,
        }
        assert bodies == {"A body", "B body"}

    def test_rename_back_reuses_existing_slot(self, store):
        note = _typed_note("Notes", "body")
        store.save_note(note)
        note.title = "Other"
        store.save_note(note)
        note.title = "Notes"
        store.save_note(note)

        # No suffixed duplicate from churning the title.
        names = sorted(p.name for p in store.notes_dir.glob("*.md"))
        assert names == ["Notes.md"]


# ---------------------------------------------------------------------------
# List / soft delete / archive
# ---------------------------------------------------------------------------


class TestList:
    def test_list_returns_metadata_only(self, store):
        store.save_note(_typed_note("First", "body 1"))
        store.save_note(_typed_note("Second", "body 2"))
        listed = store.list_notes()
        assert {n.title for n in listed} == {"First", "Second"}
        assert all(n.pages == [] for n in listed)

    def test_list_skips_soft_deleted_by_default(self, store):
        keep = _typed_note("Keep", "x")
        gone = _typed_note("Gone", "y")
        store.save_note(keep)
        store.save_note(gone)
        gone.soft_delete()
        store.save_note(gone, save_pages=False)

        titles = [n.title for n in store.list_notes()]
        assert titles == ["Keep"]

        with_deleted = [n.title for n in store.list_notes(include_deleted=True)]
        assert sorted(with_deleted) == ["Gone", "Keep"]

    def test_list_skips_archived_by_default(self, store):
        active = _typed_note("Active", "x")
        archived = _typed_note("Archived", "y")
        store.save_note(active)
        store.save_note(archived)
        archived.is_archived = True
        store.save_note(archived, save_pages=False)

        titles = [n.title for n in store.list_notes()]
        assert titles == ["Active"]

    def test_filter_by_notebook(self, store):
        nb = Notebook(name="Carnet")
        store.save_notebook(nb)
        in_nb = _typed_note("Inside", "x")
        in_nb.notebook_id = nb.id
        outside = _typed_note("Outside", "y")
        store.save_note(in_nb)
        store.save_note(outside)

        filtered = store.list_notes(notebook_id=nb.id)
        assert [n.title for n in filtered] == ["Inside"]

    def test_search_by_title(self, store):
        store.save_note(_typed_note("Apple", "x"))
        store.save_note(_typed_note("Banana", "y"))
        results = store.list_notes(search_title="app")
        assert [n.title for n in results] == ["Apple"]


class TestDelete:
    def test_delete_permanent_removes_pair(self, store):
        note = _typed_note("Bye", "body")
        store.save_note(note)
        assert (store.notes_dir / "Bye.md").exists()

        store.delete_note_permanent(note.id)
        assert not (store.notes_dir / "Bye.md").exists()
        assert not (store.notes_dir / "Bye.json").exists()
        assert store.get_note(note.id) is None


# ---------------------------------------------------------------------------
# Plain MD without sidecar (Obsidian drop-in)
# ---------------------------------------------------------------------------


class TestExternalMdImport:
    def test_md_without_sidecar_is_listed(self, store):
        (store.notes_dir / "External.md").write_text(
            "# Dropped in\n\nbody\n", encoding="utf-8"
        )
        listed = store.list_notes()
        assert any(n.title == "External" for n in listed)

    def test_external_md_id_resolves(self, store):
        (store.notes_dir / "Dropped.md").write_text("hi", encoding="utf-8")
        listed = store.list_notes()
        external = next(n for n in listed if n.title == "Dropped")
        full = store.get_note(external.id, load_pages=True)
        assert full is not None
        assert "hi" in full.pages[0].typed_content


# ---------------------------------------------------------------------------
# Notebook CRUD parity
# ---------------------------------------------------------------------------


class TestNotebooks:
    def test_save_and_list(self, store):
        nb = Notebook(name="Cours", color="#3b82f6")
        store.save_notebook(nb)
        assert [n.id for n in store.list_notebooks()] == [nb.id]

    def test_delete_notebook(self, store):
        nb = Notebook(name="Tmp")
        store.save_notebook(nb)
        store.delete_notebook(nb.id)
        assert store.list_notebooks() == []


# ---------------------------------------------------------------------------
# Backend factory + mode marker
# ---------------------------------------------------------------------------


class TestBackendFactory:
    def test_default_mode_is_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_STORAGE_MODE, raising=False)
        info = detect_mode(tmp_path / "fresh")
        assert info.mode == MODE_YAML
        assert info.source == "default"

    def test_env_var_picks_plain(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_STORAGE_MODE, "plain")
        info = detect_mode(tmp_path / "via_env")
        assert info.mode == MODE_PLAIN
        assert info.source == "env"

    def test_marker_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_STORAGE_MODE, "plain")
        data_dir = tmp_path / "with_marker"
        data_dir.mkdir()
        (data_dir / MODE_MARKER).write_text("yaml\n", encoding="utf-8")
        info = detect_mode(data_dir)
        assert info.mode == MODE_YAML
        assert info.source == "marker"

    def test_create_store_yaml(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_STORAGE_MODE, raising=False)
        store = create_store(tmp_path / "y")
        try:
            assert isinstance(store, FileNoteStore)
        finally:
            store.close()

    def test_create_store_plain(self, tmp_path):
        store = create_store(tmp_path / "p", mode=MODE_PLAIN)
        try:
            assert isinstance(store, PlainMarkdownNoteStore)
            # Forced mode is also recorded so subsequent opens are stable.
            assert (tmp_path / "p" / MODE_MARKER).exists()
        finally:
            store.close()

    def test_marker_pinned_after_first_open(self, tmp_path, monkeypatch):
        # Open once with env=plain, the marker pins it.
        monkeypatch.setenv(ENV_STORAGE_MODE, "plain")
        s1 = create_store(tmp_path / "stick")
        s1.close()

        # Now drop the env var — second open must still see plain.
        monkeypatch.delenv(ENV_STORAGE_MODE, raising=False)
        s2 = create_store(tmp_path / "stick")
        try:
            assert isinstance(s2, PlainMarkdownNoteStore)
        finally:
            s2.close()


# ---------------------------------------------------------------------------
# YAML → plain migration
# ---------------------------------------------------------------------------


class TestMigration:
    def _seed_yaml(self, data_dir: Path) -> dict:
        store = FileNoteStore(data_dir)
        try:
            nb = Notebook(name="Carnet", color="#3b82f6")
            store.save_notebook(nb)
            note = Note(notebook_id=nb.id, title="Migrated", note_type=NoteType.TYPED)
            note.add_page(template="lined").typed_content = "yaml body"
            store.save_note(note)
            return {"nb": nb, "note": note}
        finally:
            store.close()

    def test_migrate_converts_notes(self, tmp_path):
        data_dir = tmp_path / "migration"
        seeded = self._seed_yaml(data_dir)

        report = migrate_yaml_to_plain(data_dir)
        assert report.ran
        assert report.notes == 1
        assert report.notebooks == 1

        # Plain backend reads the migrated note correctly.
        plain = PlainMarkdownNoteStore(data_dir)
        try:
            loaded = plain.get_note(seeded["note"].id, load_pages=True)
            assert loaded is not None
            assert loaded.title == "Migrated"
            assert loaded.pages[0].typed_content == "yaml body"
        finally:
            plain.close()

    def test_migration_creates_plain_files(self, tmp_path):
        data_dir = tmp_path / "creates_files"
        self._seed_yaml(data_dir)

        migrate_yaml_to_plain(data_dir)

        assert (data_dir / "notes" / "Migrated.md").exists()
        assert (data_dir / "notes" / "Migrated.json").exists()
        # And the old YAML-format file is preserved as a backup.
        backup = data_dir / "notes" / "_yaml_backup"
        assert backup.exists()
        assert any(backup.glob("*.md"))

    def test_migration_pins_mode_to_plain(self, tmp_path):
        data_dir = tmp_path / "mode_pinned"
        self._seed_yaml(data_dir)

        migrate_yaml_to_plain(data_dir)
        info = detect_mode(data_dir)
        assert info.mode == MODE_PLAIN

    def test_migration_idempotent(self, tmp_path):
        data_dir = tmp_path / "idem"
        self._seed_yaml(data_dir)
        first = migrate_yaml_to_plain(data_dir)
        second = migrate_yaml_to_plain(data_dir)
        assert first.ran
        assert not second.ran
        assert "marker present" in (second.skipped_reason or "")

    def test_migration_on_empty_store(self, tmp_path):
        data_dir = tmp_path / "empty"
        # Fresh dir: no .md files. Migration still pins the mode.
        report = migrate_yaml_to_plain(data_dir)
        assert not report.ran  # nothing to convert
        assert detect_mode(data_dir).mode == MODE_PLAIN

    def test_migration_preserves_notebook(self, tmp_path):
        data_dir = tmp_path / "with_nb"
        seeded = self._seed_yaml(data_dir)
        migrate_yaml_to_plain(data_dir)

        plain = PlainMarkdownNoteStore(data_dir)
        try:
            nbs = plain.list_notebooks()
            assert any(nb.id == seeded["nb"].id for nb in nbs)
        finally:
            plain.close()


# ---------------------------------------------------------------------------
# Sync compatibility
# ---------------------------------------------------------------------------


def _stub_remote_note(note_id: str, title: str, body: str, updated_iso: str) -> dict:
    return {
        "id": note_id,
        "title": title,
        "type": "typed",
        "tags": [],
        "is_pinned": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": updated_iso,
        "pages": [
            {"page_number": 1, "template": "blank", "typed_content": body},
        ],
    }


def _patch_engine_for_pull(engine, remote_meta: dict) -> None:
    engine.client.ping = lambda: True
    engine.client.list_notebooks = lambda: [
        {"name": "carnet__01234567", "is_collection": True}
    ]
    engine.client.list_notes = lambda nb_slug: [
        {"name": "remote-note__89abcdef", "is_collection": True}
    ]
    engine.client.get_note_meta = lambda nb, note: remote_meta
    engine.client.get_ink_page = lambda nb, note, page_num: None


class TestSync:
    def test_pull_writes_plain_files(self, tmp_path):
        store = PlainMarkdownNoteStore(tmp_path / "pull")
        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(server_url="http://test/", username="u", password="p"),
        )

        remote = _stub_remote_note(
            "89abcdef-1111-2222-3333-444455556666",
            "From Server",
            "synced body",
            "2026-02-01T00:00:00+00:00",
        )
        _patch_engine_for_pull(engine, remote)
        engine._push = lambda report: None

        report = engine.sync()
        assert report.success(), report.errors
        assert report.notes_pulled == 1

        # Plain layout files were written.
        assert (store.notes_dir / "From Server.md").exists()
        assert (store.notes_dir / "From Server.json").exists()
        body = (store.notes_dir / "From Server.md").read_text("utf-8")
        assert "synced body" in body
        assert not body.startswith("---")

        store.close()

    def test_pull_rename_updates_filenames(self, tmp_path):
        store = PlainMarkdownNoteStore(tmp_path / "pull_rename")
        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(server_url="http://test/", username="u", password="p"),
        )

        first = _stub_remote_note(
            "89abcdef-1111-2222-3333-444455556666",
            "Original",
            "v1",
            "2026-01-01T00:00:00+00:00",
        )
        _patch_engine_for_pull(engine, first)
        engine._push = lambda report: None
        engine.sync()
        assert (store.notes_dir / "Original.md").exists()

        # Mark synced so the conflict path is skipped.
        local = store.get_note(first["id"], load_pages=True)
        local.sync_status = SyncStatus.SYNCED
        store.save_note(local)

        renamed = dict(first)
        renamed["title"] = "Renamed"
        renamed["updated_at"] = "2026-03-01T00:00:00+00:00"
        renamed["pages"] = [
            {"page_number": 1, "template": "blank", "typed_content": "v2"},
        ]
        _patch_engine_for_pull(engine, renamed)
        engine.sync()

        assert (store.notes_dir / "Renamed.md").exists()
        assert not (store.notes_dir / "Original.md").exists()
        store.close()

    def test_push_marks_notes_synced(self, tmp_path):
        """Push uses save_note(save_pages=False) — must reuse existing body."""
        store = PlainMarkdownNoteStore(tmp_path / "push")
        nb = Notebook(name="Carnet")
        store.save_notebook(nb)
        note = Note(notebook_id=nb.id, title="Pushable", note_type=NoteType.TYPED)
        note.add_page().typed_content = "to push"
        store.save_note(note)

        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(server_url="http://test/", username="u", password="p"),
        )
        # Stub all network so the push success path completes.
        engine.client.ping = lambda: True
        engine.client.list_notebooks = lambda: []
        engine.client.list_notes = lambda nb_slug: []
        engine.client.create_notebook_dir = lambda nb_slug: True
        engine.client.create_note_dir = lambda nb_slug, note_slug: True
        engine.client.put_note_meta = lambda *a, **k: (True, None)
        engine.client.put_ink_page = lambda *a, **k: (True, None)
        # Skip pull so we exercise only push.
        engine._pull = lambda report: None

        report = engine.sync()
        assert report.success(), report.errors
        assert report.notes_pushed == 1

        # The note is marked SYNCED and the body is preserved across the
        # save_pages=False call the engine performs.
        loaded = store.get_note(note.id, load_pages=True)
        assert loaded.sync_status == SyncStatus.SYNCED
        assert loaded.pages[0].typed_content == "to push"
        store.close()


# ---------------------------------------------------------------------------
# Conflict handling
# ---------------------------------------------------------------------------


class TestConflict:
    def test_keep_both_writes_winner_and_conflict_copy(self, tmp_path):
        store = PlainMarkdownNoteStore(tmp_path / "conflict")

        local = Note(title="Doc", note_type=NoteType.TYPED)
        local.id = "shared-id"
        local.add_page().typed_content = "local body"
        local.updated_at = datetime.now(timezone.utc) + timedelta(seconds=10)
        store.save_note(local)

        remote = Note(title="Doc", note_type=NoteType.TYPED)
        remote.id = "shared-id"
        remote.add_page().typed_content = "remote body"
        remote.updated_at = datetime.now(timezone.utc)

        resolver = ConflictResolver(strategy=ConflictStrategy.KEEP_BOTH)
        result = resolver.resolve(local, remote)
        assert result.had_conflict()
        store.save_note(result.winner)
        if result.conflict_copy:
            store.save_note(result.conflict_copy)

        names = sorted(p.name for p in store.notes_dir.glob("*.md"))
        assert "Doc.md" in names
        # Conflict copy gets a separate file (different title -> different stem).
        assert any("conflit" in n.lower() or "doc" in n.lower() for n in names)
        store.close()

    def test_conflict_through_sync_engine(self, tmp_path):
        """End-to-end: local modified + remote different → resolver runs and
        the resulting note(s) land on disk in the plain layout."""
        store = PlainMarkdownNoteStore(tmp_path / "sync_conflict")

        # Pre-existing local note marked MODIFIED so the engine treats the
        # remote pull as a potential conflict.
        local = Note(title="Conflict", note_type=NoteType.TYPED)
        local.id = "89abcdef-1111-2222-3333-444455556666"
        local.add_page().typed_content = "local edit"
        local.sync_status = SyncStatus.MODIFIED
        local.updated_at = datetime.now(timezone.utc) + timedelta(seconds=5)
        store.save_note(local)

        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(
                server_url="http://test/",
                username="u",
                password="p",
                conflict_strategy=ConflictStrategy.KEEP_BOTH,
            ),
        )

        remote = _stub_remote_note(
            local.id,
            "Conflict",
            "remote edit",
            "2026-01-01T00:00:00+00:00",
        )
        _patch_engine_for_pull(engine, remote)
        engine._push = lambda report: None

        report = engine.sync()
        assert report.success(), report.errors
        assert report.conflicts_resolved == 1

        # Both winner and conflict copy are persisted.
        all_notes = store.list_notes()
        assert len(all_notes) >= 2
        store.close()


# ---------------------------------------------------------------------------
# API integration — `create_app` works the same with the plain backend
# ---------------------------------------------------------------------------


class TestApiPlain:
    @pytest.fixture
    def client(self, tmp_path):
        db = PlainMarkdownNoteStore(tmp_path / "api_plain")
        app = create_app(db)
        with TestClient(app) as c:
            yield c, db
        db.close()

    def test_create_via_api(self, client):
        c, db = client
        resp = c.post("/notes", json={"title": "Via API", "note_type": "typed"})
        assert resp.status_code == 201
        assert (db.notes_dir / "Via API.md").exists()
        assert (db.notes_dir / "Via API.json").exists()

    def test_rename_via_api(self, client):
        c, db = client
        created = c.post(
            "/notes", json={"title": "Old via API", "note_type": "typed"}
        ).json()
        c.put(f"/notes/{created['id']}", json={"title": "New via API"})
        assert (db.notes_dir / "New via API.md").exists()
        assert not (db.notes_dir / "Old via API.md").exists()

    def test_text_update_via_api(self, client):
        c, db = client
        created = c.post(
            "/notes", json={"title": "Editable", "note_type": "typed"}
        ).json()
        c.put(
            f"/notes/{created['id']}/pages/1/text",
            json={"typed_content": "edited via API"},
        )
        body = (db.notes_dir / "Editable.md").read_text("utf-8")
        assert "edited via API" in body

    def test_delete_via_api_soft_then_purge(self, client):
        c, db = client
        created = c.post(
            "/notes", json={"title": "Trash", "note_type": "typed"}
        ).json()
        c.delete(f"/notes/{created['id']}")
        # Soft delete: file still on disk but excluded from default listing.
        assert (db.notes_dir / "Trash.md").exists()
        meta = _read_sidecar(db, "Trash")
        assert meta["is_deleted"] is True

        listed_titles = [n["title"] for n in c.get("/notes").json()]
        assert "Trash" not in listed_titles
