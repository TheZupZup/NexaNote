"""
NexaNote — Tests for the automatic clean Markdown export.

EN: Auto-export keeps an Obsidian-friendly mirror of the user's notes
    next to the internal storage. These tests cover the four trigger
    points called out in the spec:
      - note create
      - note update / save
      - note title change
      - sync pull (via the engine's call to ``save_note``)
    plus the safety guarantees: feature-off by default, archived/deleted
    notes are not exported, duplicate titles are suffixed, and the
    internal storage is never touched.

FR: Tests pour l'export Markdown automatique. Vérifient les déclencheurs
    requis (création, sauvegarde, changement de titre, sync pull) ainsi
    que les garanties (désactivé par défaut, suppressions et collisions
    gérées proprement, stockage interne intact).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.models.note import (
    InkStroke,
    Note,
    NoteType,
    Page,
    Point,
    SyncStatus,
)
from nexanote.storage import (
    AutoExportConfig,
    AutoExporter,
    FileNoteStore,
)
from nexanote.storage.export import (
    ENV_AUTO_EXPORT,
    ENV_EXPORT_DIR,
    INDEX_FILE,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def export_dir(tmp_path) -> Path:
    return tmp_path / "obsidian-mirror"


@pytest.fixture
def store(tmp_path, export_dir) -> FileNoteStore:
    """Store with auto-export enabled by an explicit config."""
    config = AutoExportConfig(enabled=True, target_dir=export_dir)
    s = FileNoteStore(tmp_path / "store", auto_export=config)
    yield s
    s.close()


@pytest.fixture
def disabled_store(tmp_path) -> FileNoteStore:
    """Store with no auto-export configured (default behavior)."""
    s = FileNoteStore(tmp_path / "no_export_store")
    yield s
    s.close()


def _typed_note(title: str, body: str = "body") -> Note:
    note = Note(title=title, note_type=NoteType.TYPED)
    note.add_page().typed_content = body
    return note


def _read_index(target: Path) -> dict[str, str]:
    path = target / INDEX_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))["by_note_id"]


# ---------------------------------------------------------------------------
# Config: env var parsing
# ---------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_disabled_by_default(self):
        config = AutoExportConfig.from_env(default_dir=Path("/tmp/x"), env={})
        assert config.enabled is False

    def test_enabled_when_env_truthy(self):
        for value in ("true", "1", "yes", "on", "TRUE", "  True  "):
            config = AutoExportConfig.from_env(
                default_dir=Path("/tmp/x"),
                env={ENV_AUTO_EXPORT: value},
            )
            assert config.enabled, f"value={value!r} should enable auto-export"

    def test_disabled_when_env_falsy(self):
        for value in ("false", "0", "no", "off", "", "garbage"):
            config = AutoExportConfig.from_env(
                default_dir=Path("/tmp/x"),
                env={ENV_AUTO_EXPORT: value},
            )
            assert not config.enabled, f"value={value!r} should leave auto-export off"

    def test_target_dir_from_env(self, tmp_path):
        config = AutoExportConfig.from_env(
            default_dir=tmp_path / "default",
            env={
                ENV_AUTO_EXPORT: "true",
                ENV_EXPORT_DIR: str(tmp_path / "custom"),
            },
        )
        assert config.target_dir == tmp_path / "custom"

    def test_default_dir_used_when_var_unset(self, tmp_path):
        config = AutoExportConfig.from_env(
            default_dir=tmp_path / "fallback",
            env={ENV_AUTO_EXPORT: "true"},
        )
        assert config.target_dir == tmp_path / "fallback"


class TestStoreReadsEnv:
    """The store reads env vars when no explicit config is given."""

    def test_store_disabled_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_AUTO_EXPORT, raising=False)
        monkeypatch.delenv(ENV_EXPORT_DIR, raising=False)
        store = FileNoteStore(tmp_path / "store")
        try:
            assert store.auto_exporter.enabled is False
            store.save_note(_typed_note("Anything", "body"))
            # Default fallback dir is `<data_dir>/export` — but with the
            # exporter disabled, nothing should be created there.
            export_dir = tmp_path / "store" / "export"
            assert not export_dir.exists() or not any(export_dir.glob("*.md"))
        finally:
            store.close()

    def test_store_enabled_via_env(self, tmp_path, monkeypatch):
        target = tmp_path / "env-mirror"
        monkeypatch.setenv(ENV_AUTO_EXPORT, "true")
        monkeypatch.setenv(ENV_EXPORT_DIR, str(target))

        store = FileNoteStore(tmp_path / "store")
        try:
            assert store.auto_exporter.enabled
            store.save_note(_typed_note("Hello env", "from env"))
            assert (target / "Hello env.md").exists()
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Triggers required by the spec
# ---------------------------------------------------------------------------


class TestAutoExportOnCreate:
    def test_save_note_creates_clean_md_file(self, store, export_dir):
        note = _typed_note("First Note", "# Hello\n\nbody text")
        store.save_note(note)

        path = export_dir / "First Note.md"
        assert path.exists(), "auto-export should create the mirror file on save"
        text = path.read_text(encoding="utf-8")
        assert not text.startswith("---"), "exported file must have no frontmatter"
        assert "# Hello" in text
        assert "body text" in text

    def test_index_records_filename(self, store, export_dir):
        note = _typed_note("Indexed", "x")
        store.save_note(note)
        index = _read_index(export_dir)
        assert index.get(note.id) == "Indexed.md"

    def test_internal_storage_untouched(self, store, export_dir):
        note = _typed_note("Untouched", "body")
        store.save_note(note)

        # Internal file keeps the YAML frontmatter.
        internal = store._note_path(note.id)
        text = internal.read_text(encoding="utf-8")
        assert text.startswith("---\n")

        # Exported file has no frontmatter.
        exported = (export_dir / "Untouched.md").read_text(encoding="utf-8")
        assert not exported.startswith("---")


class TestAutoExportOnUpdate:
    def test_save_overwrites_same_file(self, store, export_dir):
        note = _typed_note("Doc", "first version")
        store.save_note(note)

        # Edit body and save again.
        note.pages[0].typed_content = "second version"
        store.save_note(note)

        path = export_dir / "Doc.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "second version" in text
        assert "first version" not in text

        # Only one file in the mirror — no `Doc (2).md` from re-save.
        md_files = sorted(p.name for p in export_dir.glob("*.md"))
        assert md_files == ["Doc.md"]

    def test_metadata_only_save_still_exports_body(self, store, export_dir):
        """`save_note(save_pages=False)` should reuse the existing pages
        and the export must still reflect the saved body."""
        note = _typed_note("Meta", "body content")
        store.save_note(note)

        # Reload metadata only and re-save (mirrors the API's update_note path).
        meta_only = store.get_note(note.id, load_pages=False)
        meta_only.tags = ["updated"]
        store.save_note(meta_only, save_pages=False)

        text = (export_dir / "Meta.md").read_text(encoding="utf-8")
        assert "body content" in text

    def test_save_page_propagates_to_export(self, store, export_dir):
        """Editing a page (the route used by the editor) must keep the
        Obsidian mirror current."""
        note = _typed_note("Live", "initial")
        store.save_note(note)

        page = store.get_note(note.id, load_pages=True).pages[0]
        page.typed_content = "edited via save_page"
        store.save_page(page)

        text = (export_dir / "Live.md").read_text(encoding="utf-8")
        assert "edited via save_page" in text


class TestAutoExportOnTitleChange:
    def test_renaming_moves_the_export_file(self, store, export_dir):
        note = _typed_note("Old Title", "body")
        store.save_note(note)
        assert (export_dir / "Old Title.md").exists()

        note.title = "New Title"
        store.save_note(note)

        assert (export_dir / "New Title.md").exists(), (
            "after a title change the new file must appear"
        )
        assert not (export_dir / "Old Title.md").exists(), (
            "the previous export must be removed to avoid stale duplicates"
        )

        # Index is updated.
        assert _read_index(export_dir)[note.id] == "New Title.md"

    def test_invalid_chars_in_renamed_title_sanitized(self, store, export_dir):
        note = _typed_note("Clean", "body")
        store.save_note(note)

        note.title = "weird/title:?<>"
        store.save_note(note)

        names = sorted(p.name for p in export_dir.glob("*.md"))
        assert all(ch not in name for name in names for ch in '/:?<>"\\|*')
        assert any(name.endswith(".md") for name in names)


class TestAutoExportOnSyncPull:
    """
    EN: The sync engine's pull path calls ``save_note(remote_note)``. We
        exercise the full ``engine.sync()`` with a stubbed WebDAV client so
        the test is hermetic but still proves the pull → save → export
        wiring end-to-end.
    """

    def _patch_client(self, engine, remote_meta: dict) -> None:
        """Stub every network method the engine uses so no socket is opened."""
        engine.client.ping = lambda: True
        engine.client.list_notebooks = lambda: [
            {"name": "carnet__01234567", "is_collection": True}
        ]
        engine.client.list_notes = lambda nb_slug: [
            {"name": "remote-note__89abcdef", "is_collection": True}
        ]
        engine.client.get_note_meta = lambda nb, note: remote_meta
        engine.client.get_ink_page = lambda nb, note, page_num: None

    def test_pull_writes_clean_md(self, store, export_dir):
        from nexanote.sync.client import NexaNoteSyncEngine, SyncConfig

        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(server_url="http://test.invalid/", username="u", password="p"),
        )

        remote_meta = {
            "id": "89abcdef-1111-2222-3333-444455556666",
            "title": "Pulled From Server",
            "type": "typed",
            "tags": ["sync"],
            "is_pinned": False,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "pages": [
                {"page_number": 1, "template": "blank", "typed_content": "from sync pull"},
            ],
        }
        self._patch_client(engine, remote_meta)

        # Skip push — we only care about pull behavior.
        engine._push = lambda report: None

        report = engine.sync()
        assert report.success(), f"sync failed: {report.errors}"
        assert report.notes_pulled == 1

        path = export_dir / "Pulled From Server.md"
        assert path.exists(), "auto-export must run on the pulled note"
        assert "from sync pull" in path.read_text(encoding="utf-8")

    def test_pull_update_renames_export(self, store, export_dir):
        """A subsequent pull with a newer timestamp + new title must move the
        mirror file the same way a local rename does."""
        from nexanote.sync.client import NexaNoteSyncEngine, SyncConfig

        engine = NexaNoteSyncEngine(
            store,
            SyncConfig(server_url="http://test.invalid/", username="u", password="p"),
        )

        first = {
            "id": "89abcdef-1111-2222-3333-444455556666",
            "title": "Original",
            "type": "typed",
            "tags": [],
            "is_pinned": False,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "pages": [
                {"page_number": 1, "template": "blank", "typed_content": "v1"},
            ],
        }
        self._patch_client(engine, first)
        engine._push = lambda report: None
        engine.sync()
        assert (export_dir / "Original.md").exists()

        # Mark local as SYNCED so the engine treats the remote-newer branch
        # as a plain update (not a conflict).
        local = store.get_note(first["id"], load_pages=True)
        local.sync_status = SyncStatus.SYNCED
        store.save_note(local)

        # Server now serves a newer version with a different title.
        second = dict(first)
        second["title"] = "Renamed Server-side"
        second["updated_at"] = "2026-02-01T00:00:00+00:00"
        second["pages"] = [
            {"page_number": 1, "template": "blank", "typed_content": "v2"},
        ]
        self._patch_client(engine, second)
        engine.sync()

        assert (export_dir / "Renamed Server-side.md").exists()
        assert not (export_dir / "Original.md").exists(), (
            "stale mirror from before the rename must be cleaned up"
        )


# ---------------------------------------------------------------------------
# Skip rules + duplicate handling
# ---------------------------------------------------------------------------


class TestSkipRules:
    def test_soft_deleted_note_not_exported(self, store, export_dir):
        note = _typed_note("Trash", "x")
        note.soft_delete()
        store.save_note(note, save_pages=False)
        assert not list(export_dir.glob("*.md")), (
            "soft-deleted notes must not appear in the Obsidian mirror"
        )

    def test_soft_delete_after_export_removes_file(self, store, export_dir):
        note = _typed_note("ToDelete", "body")
        store.save_note(note)
        assert (export_dir / "ToDelete.md").exists()

        note.soft_delete()
        store.save_note(note, save_pages=False)

        assert not (export_dir / "ToDelete.md").exists()
        assert _read_index(export_dir).get(note.id) is None

    def test_archived_note_not_exported(self, store, export_dir):
        note = _typed_note("Archived", "x")
        note.is_archived = True
        store.save_note(note, save_pages=False)
        assert not list(export_dir.glob("*.md"))

    def test_restore_re_creates_export(self, store, export_dir):
        note = _typed_note("Comes Back", "body")
        store.save_note(note)
        note.soft_delete()
        store.save_note(note, save_pages=False)
        assert not (export_dir / "Comes Back.md").exists()

        note.restore()
        store.save_note(note, save_pages=False)
        assert (export_dir / "Comes Back.md").exists()

    def test_hard_delete_removes_export(self, store, export_dir):
        note = _typed_note("Goodbye", "body")
        store.save_note(note)
        assert (export_dir / "Goodbye.md").exists()

        store.delete_note_permanent(note.id)
        assert not (export_dir / "Goodbye.md").exists()


class TestDuplicateTitles:
    def test_two_notes_same_title_get_distinct_files(self, store, export_dir):
        a = _typed_note("Same", "body A")
        b = _typed_note("Same", "body B")
        store.save_note(a)
        store.save_note(b)

        names = sorted(p.name for p in export_dir.glob("*.md"))
        assert names == ["Same (2).md", "Same.md"]

        index = _read_index(export_dir)
        assert sorted(index.values()) == ["Same (2).md", "Same.md"]
        assert index[a.id] != index[b.id]

    def test_repeated_save_does_not_grow_suffix(self, store, export_dir):
        note = _typed_note("Stable", "body")
        for i in range(5):
            note.pages[0].typed_content = f"version {i}"
            store.save_note(note)

        names = sorted(p.name for p in export_dir.glob("*.md"))
        assert names == ["Stable.md"], (
            "a note saved repeatedly must keep occupying the same filename"
        )

    def test_does_not_overwrite_user_file(self, store, export_dir):
        export_dir.mkdir(parents=True, exist_ok=True)
        kept = export_dir / "Notes.md"
        kept.write_text("user-managed content\n", encoding="utf-8")

        note = _typed_note("Notes", "from NexaNote")
        store.save_note(note)

        assert kept.read_text(encoding="utf-8") == "user-managed content\n"
        assert (export_dir / "Notes (2).md").exists()
        assert "from NexaNote" in (export_dir / "Notes (2).md").read_text("utf-8")


# ---------------------------------------------------------------------------
# Disabled exporter is a complete no-op
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_disabled_store_writes_nothing(self, tmp_path, disabled_store):
        disabled_store.save_note(_typed_note("Anything", "body"))
        # No file in the default `<data_dir>/export` location.
        export_dir = tmp_path / "no_export_store" / "export"
        if export_dir.exists():
            assert not list(export_dir.glob("*.md"))

    def test_disabled_remove_is_safe(self, disabled_store):
        # Should not raise even with no target dir / no index.
        disabled_store.auto_exporter.remove("missing-id")


# ---------------------------------------------------------------------------
# WebDAV sync compat — ensure auto-export does not break the existing path
# ---------------------------------------------------------------------------


class TestWebDAVCompat:
    """
    EN: The WebDAV provider also writes notes via ``save_note``. Auto-export
        must layer on top without changing the on-disk note format that the
        provider serves to clients.
    """

    def test_internal_format_unchanged_with_auto_export(self, tmp_path):
        # First, capture the on-disk format produced with auto-export OFF.
        plain_store = FileNoteStore(tmp_path / "plain")
        try:
            note = _typed_note("Compat", "body")
            plain_store.save_note(note)
            plain_bytes = plain_store._note_path(note.id).read_bytes()
        finally:
            plain_store.close()

        # Same note, but with auto-export ON — internal bytes must match.
        config = AutoExportConfig(enabled=True, target_dir=tmp_path / "mirror")
        store = FileNoteStore(tmp_path / "with_export", auto_export=config)
        try:
            note2 = _typed_note("Compat", "body")
            note2.id = note.id  # keep the same id so timestamps match
            note2.created_at = note.created_at
            note2.updated_at = note.updated_at
            for orig_page, new_page in zip(note.pages, note2.pages):
                new_page.id = orig_page.id
                new_page.created_at = orig_page.created_at
                new_page.updated_at = orig_page.updated_at
            store.save_note(note2)
            with_export_bytes = store._note_path(note2.id).read_bytes()
        finally:
            store.close()

        assert plain_bytes == with_export_bytes, (
            "auto-export must not alter the internal Markdown representation"
        )


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------


class TestApiIntegration:
    @pytest.fixture
    def api_client(self, tmp_path):
        target = tmp_path / "api-mirror"
        config = AutoExportConfig(enabled=True, target_dir=target)
        db = FileNoteStore(tmp_path / "api_store", auto_export=config)
        app = create_app(db)
        with TestClient(app) as c:
            yield c, db, target
        db.close()

    def test_create_via_api_exports(self, api_client):
        c, db, target = api_client
        resp = c.post("/notes", json={"title": "API note", "note_type": "typed"})
        assert resp.status_code == 201
        assert (target / "API note.md").exists()

    def test_update_title_via_api_renames_export(self, api_client):
        c, db, target = api_client
        created = c.post("/notes", json={"title": "Old", "note_type": "typed"}).json()
        c.put(f"/notes/{created['id']}", json={"title": "Brand New"})

        assert (target / "Brand New.md").exists()
        assert not (target / "Old.md").exists()

    def test_text_update_via_api_refreshes_export(self, api_client):
        c, db, target = api_client
        created = c.post("/notes", json={"title": "Live edit", "note_type": "typed"}).json()
        c.put(
            f"/notes/{created['id']}/pages/1/text",
            json={"typed_content": "fresh body via API"},
        )
        text = (target / "Live edit.md").read_text(encoding="utf-8")
        assert "fresh body via API" in text

    def test_delete_via_api_removes_export(self, api_client):
        c, db, target = api_client
        created = c.post("/notes", json={"title": "Bye", "note_type": "typed"}).json()
        assert (target / "Bye.md").exists()

        c.delete(f"/notes/{created['id']}")
        assert not (target / "Bye.md").exists()
