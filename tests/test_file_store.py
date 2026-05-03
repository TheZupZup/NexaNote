"""
Tests NexaNote — Stockage fichier (Markdown + drawings JSON).
Couvre la sérialisation .md, la séparation des pages, et la concurrence.
"""

import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import yaml

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Point
from nexanote.storage import FileNoteStore
from nexanote.storage.file_store import (
    deserialize_note,
    serialize_note,
    _split_pages_body,
    _join_pages_body,
    _split_frontmatter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = FileNoteStore(tmp_path / "store")
    yield s
    s.close()


def _typed_note(title: str = "Test", content: str = "Hello world") -> Note:
    note = Note(title=title, note_type=NoteType.TYPED)
    page = note.add_page(template="lined")
    page.typed_content = content
    return note


def _handwritten_note(title: str = "Sketch") -> Note:
    note = Note(title=title, note_type=NoteType.HANDWRITTEN)
    page = note.add_page(template="blank")
    page.add_stroke(InkStroke(
        color="#ff0000", width=3.0, tool="pen",
        points=[Point(0, 0), Point(10, 10, 0.8), Point(20, 5, 0.6)],
    ))
    return note


# ---------------------------------------------------------------------------
# On-disk layout
# ---------------------------------------------------------------------------

class TestOnDiskLayout:
    def test_directories_created(self, store):
        assert (store.data_dir / "notes").is_dir()
        assert (store.data_dir / "drawings").is_dir()
        assert (store.data_dir / "notebooks").is_dir()

    def test_typed_note_writes_md_only(self, store):
        note = _typed_note(content="Just text, no strokes")
        store.save_note(note)

        md = store.notes_dir / f"{note.id}.md"
        drawings = store.drawings_dir / f"{note.id}.json"
        assert md.exists(), "expected markdown file to exist"
        assert not drawings.exists(), "no strokes → no drawings file"

        text = md.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "Just text, no strokes" in text

    def test_handwritten_note_writes_drawings(self, store):
        note = _handwritten_note()
        store.save_note(note)

        md = store.notes_dir / f"{note.id}.md"
        drawings = store.drawings_dir / f"{note.id}.json"
        assert md.exists()
        assert drawings.exists()

        payload = json.loads(drawings.read_text())
        assert payload["note_id"] == note.id
        assert len(payload["pages"]) == 1
        assert len(payload["pages"][0]["strokes"]) == 1

    def test_notebook_writes_yaml(self, store):
        nb = Notebook(name="My Notebook", color="#abcdef")
        store.save_notebook(nb)

        yaml_file = store.notebooks_dir / f"{nb.id}.yaml"
        assert yaml_file.exists()

        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        assert data["name"] == "My Notebook"
        assert data["color"] == "#abcdef"


# ---------------------------------------------------------------------------
# Frontmatter / Markdown round-trip
# ---------------------------------------------------------------------------

class TestMarkdownRoundtrip:
    def test_typed_note_roundtrip(self, store):
        note = _typed_note(title="Roundtrip", content="# My Note\n\nBody text.")
        note.add_tag("personal")
        store.save_note(note)

        loaded = store.get_note(note.id)
        assert loaded is not None
        assert loaded.title == "Roundtrip"
        assert loaded.tags == ["personal"]
        assert loaded.pages[0].typed_content.strip() == "# My Note\n\nBody text."

    def test_frontmatter_keys(self, store):
        note = _typed_note()
        store.save_note(note)
        meta, _ = _split_frontmatter((store.notes_dir / f"{note.id}.md").read_text())

        # Per spec: title, created_at, updated_at, tags
        for key in ("title", "created_at", "updated_at", "tags"):
            assert key in meta, f"frontmatter must include {key}"

    def test_handwritten_roundtrip(self, store):
        note = _handwritten_note(title="Strokes")
        store.save_note(note)

        loaded = store.get_note(note.id)
        assert loaded is not None
        assert len(loaded.pages) == 1
        assert len(loaded.pages[0].strokes) == 1
        assert loaded.pages[0].strokes[0].color == "#ff0000"
        assert len(loaded.pages[0].strokes[0].points) == 3

    def test_obsidian_friendly_single_page(self, store):
        """A 1-page typed note has a clean body — no NexaNote markers."""
        note = _typed_note(content="# Title\n\nObsidian-friendly")
        store.save_note(note)

        body = (store.notes_dir / f"{note.id}.md").read_text()
        # No nexanote:page markers should appear for a single-page note.
        assert "nexanote:page" not in body

    def test_external_md_edit_preserved(self, store):
        """If a user edits the .md by hand, the new content survives a reload."""
        note = _typed_note(content="initial")
        store.save_note(note)

        path = store.notes_dir / f"{note.id}.md"
        text = path.read_text()
        edited = text.replace("initial", "edited externally")
        path.write_text(edited)

        loaded = store.get_note(note.id)
        assert loaded is not None
        assert "edited externally" in loaded.pages[0].typed_content


# ---------------------------------------------------------------------------
# Multi-page splitting
# ---------------------------------------------------------------------------

class TestMultiPageBody:
    def test_split_no_markers_yields_page_one(self):
        body = "This is page 1 content.\n"
        pages = _split_pages_body(body)
        assert pages == {1: "This is page 1 content."}

    def test_split_markers(self):
        body = (
            "<!-- nexanote:page 1 -->\nhello\n\n"
            "<!-- nexanote:page 2 -->\nworld\n"
        )
        pages = _split_pages_body(body)
        assert pages == {1: "hello", 2: "world"}

    def test_join_single_page_clean(self):
        text = _join_pages_body({1: "single"})
        assert "nexanote:page" not in text
        assert text.strip() == "single"

    def test_join_multi_page_uses_markers(self):
        text = _join_pages_body({1: "one", 2: "two"})
        assert "<!-- nexanote:page 1 -->" in text
        assert "<!-- nexanote:page 2 -->" in text

    def test_multi_page_roundtrip(self, store):
        note = Note(title="Multi", note_type=NoteType.TYPED)
        note.add_page().typed_content = "First page"
        note.add_page().typed_content = "Second page"
        store.save_note(note)

        loaded = store.get_note(note.id)
        assert len(loaded.pages) == 2
        contents = [p.typed_content for p in loaded.pages]
        assert "First page" in contents[0]
        assert "Second page" in contents[1]


# ---------------------------------------------------------------------------
# Metadata-only update preserves drawings
# ---------------------------------------------------------------------------

class TestMetadataOnlyUpdate:
    def test_save_pages_false_keeps_strokes(self, store):
        note = _handwritten_note(title="Original")
        store.save_note(note)

        # Update only title
        note.title = "Renamed"
        store.save_note(note, save_pages=False)

        loaded = store.get_note(note.id)
        assert loaded.title == "Renamed"
        assert len(loaded.pages[0].strokes) == 1, "strokes must survive a metadata-only save"


# ---------------------------------------------------------------------------
# Filtering / search
# ---------------------------------------------------------------------------

class TestListFilters:
    def test_search_title_case_insensitive(self, store):
        store.save_note(_typed_note(title="Réunion équipe"))
        store.save_note(_typed_note(title="Recette pasta"))
        store.save_note(_typed_note(title="REUNION client"))

        hits = store.list_notes(search_title="réun")
        # 'réun' matches both 'Réunion' and 'REUNION' via lower()
        assert len(hits) >= 1

    def test_excludes_deleted_by_default(self, store):
        note = _typed_note()
        note.soft_delete()
        store.save_note(note)
        assert all(n.id != note.id for n in store.list_notes())
        assert any(n.id == note.id for n in store.list_notes(include_deleted=True))

    def test_filter_by_notebook(self, store):
        nb = Notebook(name="Notebook A")
        store.save_notebook(nb)

        in_nb = _typed_note(title="In Notebook")
        in_nb.notebook_id = nb.id
        store.save_note(in_nb)

        out_nb = _typed_note(title="Loose")
        store.save_note(out_nb)

        only = store.list_notes(notebook_id=nb.id)
        assert [n.id for n in only] == [in_nb.id]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_parallel_writes_to_same_note(self, store):
        """
        EN: Hammer the same note with concurrent text updates. The final file
            must be valid markdown with one of the writes' content — no
            corruption, no torn writes.
        """
        note = _typed_note(content="seed")
        store.save_note(note)

        def write(i: int) -> None:
            n = store.get_note(note.id)
            n.pages[0].typed_content = f"value-{i}"
            store.save_note(n)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = store.get_note(note.id)
        assert loaded is not None
        assert loaded.pages[0].typed_content.startswith("value-")

    def test_atomic_write_no_partial_files_left(self, store):
        note = _typed_note()
        store.save_note(note)

        leftover = [
            p for p in store.notes_dir.iterdir()
            if p.name.startswith(".") and p.name.endswith(".tmp")
        ]
        assert leftover == []


# ---------------------------------------------------------------------------
# Direct serialize / deserialize
# ---------------------------------------------------------------------------

class TestSerializerDirect:
    def test_serialize_typed_note_no_drawings(self):
        note = _typed_note(content="text only")
        md, drawings = serialize_note(note)
        assert drawings is None
        assert md.startswith("---\n")
        assert "text only" in md

    def test_serialize_handwritten_emits_drawings(self):
        note = _handwritten_note()
        md, drawings = serialize_note(note)
        assert drawings is not None
        assert drawings["schema"] == 1
        assert drawings["note_id"] == note.id

    def test_deserialize_invalid_returns_none(self):
        assert deserialize_note("no frontmatter here\n", None) is None
        assert deserialize_note("---\nfoo: bar\n---\n\n", None) is None  # missing id
