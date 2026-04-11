"""
Tests NexaNote — Modèles et base de données
Lance avec : python -m pytest tests/ -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point, SyncStatus
from nexanote.storage.database import NexaNoteDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    db = NexaNoteDB(tmp_path / "test_nexanote.db")
    yield db
    db.close()


@pytest.fixture
def sample_notebook():
    return Notebook(name="Mon carnet de tests", color="#ff5733")


@pytest.fixture
def sample_note(sample_notebook):
    note = Note(
        notebook_id=sample_notebook.id,
        title="Ma première note",
        note_type=NoteType.MIXED,
    )
    note.add_tag("test")
    note.add_tag("python")
    page = note.add_page(template="lined")
    page.typed_content = "Bonjour NexaNote !"
    stroke = InkStroke(
        color="#0000ff",
        width=3.0,
        tool="pen",
        points=[
            Point(x=10, y=20, pressure=0.5),
            Point(x=50, y=80, pressure=0.8),
            Point(x=100, y=60, pressure=0.6),
        ],
    )
    page.add_stroke(stroke)
    return note


# ---------------------------------------------------------------------------
# Tests modèles
# ---------------------------------------------------------------------------

class TestNotebook:
    def test_creation(self):
        nb = Notebook(name="Carnet 1")
        assert nb.id
        assert nb.name == "Carnet 1"
        assert nb.sync_status == SyncStatus.LOCAL_ONLY

    def test_touch_changes_sync_status(self):
        nb = Notebook()
        nb.sync_status = SyncStatus.SYNCED
        nb.touch()
        assert nb.sync_status == SyncStatus.MODIFIED


class TestNote:
    def test_creation(self, sample_note):
        assert sample_note.title == "Ma première note"
        assert sample_note.note_type == NoteType.MIXED
        assert "test" in sample_note.tags
        assert "python" in sample_note.tags

    def test_add_page(self, sample_note):
        initial = sample_note.page_count()
        sample_note.add_page()
        assert sample_note.page_count() == initial + 1

    def test_get_page(self, sample_note):
        page = sample_note.get_page(1)
        assert page is not None
        assert page.page_number == 1

    def test_soft_delete(self, sample_note):
        sample_note.soft_delete()
        assert sample_note.is_deleted is True

    def test_restore(self, sample_note):
        sample_note.soft_delete()
        sample_note.restore()
        assert sample_note.is_deleted is False

    def test_duplicate_tag_ignored(self, sample_note):
        count_before = len(sample_note.tags)
        sample_note.add_tag("test")  # Déjà présent
        assert len(sample_note.tags) == count_before

    def test_remove_tag(self, sample_note):
        sample_note.remove_tag("python")
        assert "python" not in sample_note.tags


class TestPage:
    def test_stroke_operations(self):
        page = Page(note_id="test", page_number=1)
        stroke = InkStroke(points=[
            Point(10, 20), Point(30, 40)
        ])
        page.add_stroke(stroke)
        assert page.stroke_count() == 1

        removed = page.remove_stroke(stroke.id)
        assert removed is True
        assert page.stroke_count() == 0

    def test_remove_nonexistent_stroke(self):
        page = Page(note_id="test", page_number=1)
        assert page.remove_stroke("inexistant") is False


class TestInkStroke:
    def test_bounding_box(self):
        stroke = InkStroke(points=[
            Point(10, 20), Point(50, 80), Point(30, 5)
        ])
        x_min, y_min, x_max, y_max = stroke.bounding_box()
        assert x_min == 10
        assert y_min == 5
        assert x_max == 50
        assert y_max == 80

    def test_is_empty(self):
        stroke = InkStroke(points=[Point(0, 0)])
        assert stroke.is_empty() is True

        stroke.points.append(Point(10, 10))
        assert stroke.is_empty() is False


# ---------------------------------------------------------------------------
# Tests base de données
# ---------------------------------------------------------------------------

class TestNexaNoteDB:
    def test_save_and_get_notebook(self, tmp_db, sample_notebook):
        tmp_db.save_notebook(sample_notebook)
        retrieved = tmp_db.get_notebook(sample_notebook.id)
        assert retrieved is not None
        assert retrieved.name == sample_notebook.name
        assert retrieved.color == sample_notebook.color

    def test_list_notebooks(self, tmp_db):
        for i in range(3):
            tmp_db.save_notebook(Notebook(name=f"Carnet {i}"))
        notebooks = tmp_db.list_notebooks()
        assert len(notebooks) == 3

    def test_save_and_get_note_with_pages(self, tmp_db, sample_notebook, sample_note):
        tmp_db.save_notebook(sample_notebook)
        tmp_db.save_note(sample_note)

        retrieved = tmp_db.get_note(sample_note.id, load_pages=True)
        assert retrieved is not None
        assert retrieved.title == sample_note.title
        assert len(retrieved.pages) == 1
        assert retrieved.pages[0].typed_content == "Bonjour NexaNote !"
        assert len(retrieved.pages[0].strokes) == 1
        assert retrieved.pages[0].strokes[0].color == "#0000ff"

    def test_search_notes_by_title(self, tmp_db, sample_notebook):
        tmp_db.save_notebook(sample_notebook)
        for title in ["Réunion équipe", "Recette pasta", "Réunion client"]:
            note = Note(notebook_id=sample_notebook.id, title=title)
            tmp_db.save_note(note)

        results = tmp_db.list_notes(search_title="Réunion")
        assert len(results) == 2

    def test_soft_delete_excluded_from_list(self, tmp_db, sample_notebook, sample_note):
        tmp_db.save_notebook(sample_notebook)
        sample_note.soft_delete()
        tmp_db.save_note(sample_note)

        notes = tmp_db.list_notes(include_deleted=False)
        ids = [n.id for n in notes]
        assert sample_note.id not in ids

        notes_with_deleted = tmp_db.list_notes(include_deleted=True)
        ids_with_deleted = [n.id for n in notes_with_deleted]
        assert sample_note.id in ids_with_deleted

    def test_stats(self, tmp_db, sample_notebook, sample_note):
        tmp_db.save_notebook(sample_notebook)
        tmp_db.save_note(sample_note)
        stats = tmp_db.get_stats()
        assert stats["notebooks"] == 1
        assert stats["notes"] == 1
        assert stats["pages"] == 1
        assert stats["strokes"] == 1

    def test_update_note(self, tmp_db, sample_notebook, sample_note):
        tmp_db.save_notebook(sample_notebook)
        tmp_db.save_note(sample_note)

        sample_note.title = "Titre modifié"
        tmp_db.save_note(sample_note, save_pages=False)

        retrieved = tmp_db.get_note(sample_note.id)
        assert retrieved.title == "Titre modifié"

    def test_delete_notebook(self, tmp_db, sample_notebook):
        tmp_db.save_notebook(sample_notebook)
        tmp_db.delete_notebook(sample_notebook.id)
        assert tmp_db.get_notebook(sample_notebook.id) is None
