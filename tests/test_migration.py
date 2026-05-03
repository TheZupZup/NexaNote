"""
Tests NexaNote — Migration SQLite → stockage fichier.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Point
from nexanote.storage import FileNoteStore, run_migration, needs_migration
from nexanote.storage.legacy_db import NexaNoteDB
from nexanote.storage.migration import LEGACY_DB_NAME, MIGRATION_MARKER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def legacy_db_with_data(tmp_path):
    """A pre-v1 SQLite DB pre-populated with notebooks, notes, pages, strokes."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / LEGACY_DB_NAME
    db = NexaNoteDB(db_path)

    nb = Notebook(name="Old Notebook", color="#123456")
    db.save_notebook(nb)

    typed = Note(
        notebook_id=nb.id,
        title="Typed legacy note",
        note_type=NoteType.TYPED,
    )
    typed.add_tag("legacy")
    typed.add_page(template="lined").typed_content = "Hello from SQLite."
    db.save_note(typed)

    handwritten = Note(
        notebook_id=nb.id,
        title="Stylus legacy note",
        note_type=NoteType.HANDWRITTEN,
    )
    p = handwritten.add_page(template="blank")
    p.add_stroke(InkStroke(
        color="#0000ff", width=2.0, tool="pen",
        points=[Point(0, 0), Point(10, 10, 0.7)],
    ))
    db.save_note(handwritten)

    deleted = Note(
        notebook_id=nb.id,
        title="Trashed",
        note_type=NoteType.TYPED,
    )
    deleted.add_page().typed_content = "in the bin"
    deleted.soft_delete()
    db.save_note(deleted)

    db.close()
    return data_dir, nb, typed, handwritten, deleted


# ---------------------------------------------------------------------------
# needs_migration
# ---------------------------------------------------------------------------

class TestNeedsMigration:
    def test_returns_false_on_empty_dir(self, tmp_path):
        assert needs_migration(tmp_path) is False

    def test_returns_true_when_legacy_db_present(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        assert needs_migration(data_dir) is True

    def test_returns_false_after_marker_written(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        run_migration(data_dir)
        assert needs_migration(data_dir) is False


# ---------------------------------------------------------------------------
# run_migration
# ---------------------------------------------------------------------------

class TestRunMigration:
    def test_no_op_on_fresh_install(self, tmp_path):
        report = run_migration(tmp_path)
        assert report.ran is False
        assert (tmp_path / MIGRATION_MARKER).exists(), \
            "marker should be written even when there's nothing to migrate"

    def test_idempotent(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        first = run_migration(data_dir)
        second = run_migration(data_dir)
        assert first.ran is True
        assert second.ran is False  # marker present → no-op

    def test_migrates_notebooks(self, legacy_db_with_data):
        data_dir, nb, *_ = legacy_db_with_data
        run_migration(data_dir)

        store = FileNoteStore(data_dir)
        notebooks = store.list_notebooks()
        assert len(notebooks) == 1
        assert notebooks[0].id == nb.id
        assert notebooks[0].name == "Old Notebook"

    def test_migrates_typed_note_with_text(self, legacy_db_with_data):
        data_dir, _, typed, *_ = legacy_db_with_data
        run_migration(data_dir)

        store = FileNoteStore(data_dir)
        loaded = store.get_note(typed.id)
        assert loaded is not None
        assert loaded.title == "Typed legacy note"
        assert "legacy" in loaded.tags
        assert "Hello from SQLite." in loaded.pages[0].typed_content

    def test_migrates_handwritten_strokes(self, legacy_db_with_data):
        data_dir, _, _, handwritten, _ = legacy_db_with_data
        run_migration(data_dir)

        store = FileNoteStore(data_dir)
        loaded = store.get_note(handwritten.id)
        assert loaded is not None
        assert len(loaded.pages[0].strokes) == 1
        stroke = loaded.pages[0].strokes[0]
        assert stroke.color == "#0000ff"
        assert len(stroke.points) == 2

        # Drawings file is on disk under /drawings/<id>.json
        drawings_path = data_dir / "drawings" / f"{handwritten.id}.json"
        assert drawings_path.exists()
        payload = json.loads(drawings_path.read_text())
        assert payload["note_id"] == handwritten.id

    def test_preserves_soft_deleted_notes(self, legacy_db_with_data):
        data_dir, _, _, _, deleted = legacy_db_with_data
        run_migration(data_dir)

        store = FileNoteStore(data_dir)
        # Default list excludes deleted
        assert all(n.id != deleted.id for n in store.list_notes())
        # But include_deleted surfaces it
        all_notes = store.list_notes(include_deleted=True)
        assert any(n.id == deleted.id and n.is_deleted for n in all_notes)

    def test_legacy_db_renamed_to_backup(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        run_migration(data_dir)

        original = data_dir / LEGACY_DB_NAME
        backup = data_dir / (LEGACY_DB_NAME + ".legacy_backup")
        assert not original.exists(), "legacy DB should have been moved aside"
        assert backup.exists(), "backup should be kept on disk"

    def test_marker_payload_has_counts(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        report = run_migration(data_dir)
        marker = data_dir / MIGRATION_MARKER
        payload = json.loads(marker.read_text())

        assert payload["notebooks"] == report.notebooks == 1
        # 3 notes total in fixture (typed + handwritten + soft-deleted)
        assert payload["notes"] == report.notes == 3

    def test_report_summary_human_readable(self, legacy_db_with_data):
        data_dir, *_ = legacy_db_with_data
        report = run_migration(data_dir)
        s = report.summary()
        assert "Migration done" in s
        assert "1 notebooks" in s
        assert "3 notes" in s


# ---------------------------------------------------------------------------
# End-to-end through API after migration
# ---------------------------------------------------------------------------

class TestApiAfterMigration:
    def test_api_serves_migrated_notes(self, legacy_db_with_data):
        from fastapi.testclient import TestClient
        from nexanote.api.routes import create_app

        data_dir, nb, typed, handwritten, _ = legacy_db_with_data
        run_migration(data_dir)
        store = FileNoteStore(data_dir)
        app = create_app(store)

        with TestClient(app) as c:
            resp = c.get("/notebooks")
            assert resp.status_code == 200
            assert any(n["id"] == nb.id for n in resp.json())

            resp = c.get(f"/notes/{handwritten.id}?pages=true")
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "Stylus legacy note"
            assert len(data["pages"][0]["strokes"]) == 1

        store.close()
