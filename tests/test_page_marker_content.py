"""
Note text that contains the page-marker comment must round-trip intact.
Single-page notes are stored without markers, so a marker line in the text
used to be read as a page split and everything around it was lost.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from nexanote.models.note import Note
from nexanote.storage import FileNoteStore, PlainMarkdownNoteStore

TEXT = "intro\n<!-- nexanote:page 2 -->\nmore"


@pytest.mark.parametrize("store_cls", [FileNoteStore, PlainMarkdownNoteStore])
def test_marker_line_in_single_page_note_survives(tmp_path, store_cls):
    db = store_cls(tmp_path / "store")
    note = Note(title="doc")
    note.add_page().typed_content = TEXT
    db.save_note(note)

    assert db.get_note(note.id).pages[0].typed_content == TEXT


@pytest.mark.parametrize("store_cls", [FileNoteStore, PlainMarkdownNoteStore])
def test_multi_page_notes_still_split_by_page(tmp_path, store_cls):
    db = store_cls(tmp_path / "store")
    note = Note(title="doc")
    note.add_page().typed_content = "first page"
    note.add_page().typed_content = "second page"
    db.save_note(note)

    pages = db.get_note(note.id).pages
    assert [p.typed_content for p in pages] == ["first page", "second page"]
