"""
Tests NexaNote — import de .md bruts + export Markdown propre.

EN: Covers the plain-Markdown import path (Obsidian-style files dropped
    into notes/) and the clean export pipeline (no YAML frontmatter,
    sanitized filenames, collision-free).
FR: Couvre l'import des .md bruts (style Obsidian) et l'export Markdown
    propre (sans frontmatter, avec noms de fichiers nettoyés).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from nexanote.api.routes import create_app
from nexanote.models.note import Note, NoteType
from nexanote.storage import FileNoteStore
from nexanote.storage.export import (
    export_all,
    export_note,
    sanitize_filename,
)
from nexanote.storage.file_store import (
    plain_md_id_from_stem,
    stem_from_plain_md_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = FileNoteStore(tmp_path / "store")
    yield s
    s.close()


def _seed_plain(store, name: str, body: str) -> Path:
    """Drop a plain Markdown file (no frontmatter) into the store's notes dir."""
    path = store.notes_dir / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _typed_note(title: str, body: str = "body") -> Note:
    note = Note(title=title, note_type=NoteType.TYPED)
    note.add_page().typed_content = body
    return note


# ---------------------------------------------------------------------------
# Plain MD id <-> stem round-trip
# ---------------------------------------------------------------------------

class TestPlainMdId:
    def test_roundtrip_ascii(self):
        assert stem_from_plain_md_id(plain_md_id_from_stem("Hello")) == "Hello"

    def test_roundtrip_unicode(self):
        assert stem_from_plain_md_id(plain_md_id_from_stem("Café ☕")) == "Café ☕"

    def test_roundtrip_with_spaces(self):
        assert stem_from_plain_md_id(plain_md_id_from_stem("My Notes 2025")) == "My Notes 2025"

    def test_non_plain_id_returns_none(self):
        assert stem_from_plain_md_id("not-a-plain-id") is None
        assert stem_from_plain_md_id("") is None
        assert stem_from_plain_md_id("abc-123-uuid") is None

    def test_id_survives_safe_id_filter(self):
        # The synthesized id must only contain chars that the storage layer
        # leaves untouched, otherwise _note_path would mangle the lookup.
        from nexanote.storage.file_store import _safe_id
        encoded = plain_md_id_from_stem("hello world")
        assert _safe_id(encoded) == encoded


# ---------------------------------------------------------------------------
# Plain MD import
# ---------------------------------------------------------------------------

class TestPlainMdImport:
    def test_plain_md_listed_as_note(self, store):
        _seed_plain(store, "Hello World", "# Hello\n\nThis is plain MD.")
        notes = store.list_notes()
        assert len(notes) == 1
        assert notes[0].title == "Hello World"
        assert notes[0].note_type == NoteType.TYPED

    def test_plain_md_get_returns_body(self, store):
        _seed_plain(store, "Doc", "Plain body content.")
        note_id = store.list_notes()[0].id
        full = store.get_note(note_id, load_pages=True)
        assert full is not None
        assert full.title == "Doc"
        assert len(full.pages) == 1
        assert full.pages[0].typed_content == "Plain body content."

    def test_plain_md_id_is_stable(self, store):
        _seed_plain(store, "stable", "x")
        first = store.list_notes()[0].id
        second = store.list_notes()[0].id
        assert first == second
        assert store.get_note(first) is not None

    def test_plain_md_unicode_filename(self, store):
        _seed_plain(store, "Café", "espresso")
        notes = store.list_notes()
        assert any(n.title == "Café" for n in notes)
        # Round-trip through the API id must still resolve.
        cafe = next(n for n in notes if n.title == "Café")
        assert store.get_note(cafe.id) is not None

    def test_legacy_frontmatter_md_still_loads(self, store):
        note = _typed_note("Legacy", "old style content")
        store.save_note(note)
        loaded = store.get_note(note.id, load_pages=True)
        assert loaded is not None
        assert loaded.title == "Legacy"
        assert "old style content" in loaded.pages[0].typed_content

    def test_plain_and_managed_coexist(self, store):
        managed = _typed_note("Managed", "with frontmatter")
        store.save_note(managed)
        _seed_plain(store, "Plain", "no frontmatter")

        titles = sorted(n.title for n in store.list_notes())
        assert titles == ["Managed", "Plain"]

    def test_plain_md_not_rewritten_on_read(self, store):
        path = _seed_plain(store, "untouched", "leave me alone\n")
        original = path.read_bytes()
        # Listing + fetching by id must not rewrite the file.
        store.list_notes()
        note_id = store.list_notes()[0].id
        store.get_note(note_id, load_pages=True)
        assert path.read_bytes() == original

    def test_save_converts_plain_md_in_place(self, store):
        path = _seed_plain(store, "Editme", "original body")
        note_id = store.list_notes()[0].id
        note = store.get_note(note_id, load_pages=True)
        note.pages[0].typed_content = "edited body"
        store.save_note(note)

        # Same file, now NexaNote-managed (frontmatter present).
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "edited body" in text
        # Re-read via the same id still resolves.
        again = store.get_note(note_id, load_pages=True)
        assert again is not None
        assert "edited body" in again.pages[0].typed_content

    def test_stats_count_plain_md(self, store):
        _seed_plain(store, "A", "x")
        _seed_plain(store, "B", "y")
        stats = store.get_stats()
        assert stats["notes"] >= 2


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

class TestSanitize:
    @pytest.mark.parametrize("ch", list('<>:"/\\|?*'))
    def test_strips_each_invalid_char(self, ch):
        out = sanitize_filename(f"a{ch}b")
        assert ch not in out

    def test_strips_control_chars(self):
        out = sanitize_filename("a\x00b\x01c")
        assert "\x00" not in out
        assert "\x01" not in out

    def test_blank_returns_fallback(self):
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("   ") == "untitled"
        assert sanitize_filename("///") == "untitled"

    def test_collapses_whitespace(self):
        assert sanitize_filename("hello   world") == "hello world"

    def test_truncates_long_names(self):
        out = sanitize_filename("x" * 1000)
        assert 1 <= len(out) <= 200

    def test_strips_trailing_dot_and_space(self):
        assert not sanitize_filename("name. ").endswith(".")
        assert not sanitize_filename("name. ").endswith(" ")

    def test_reserved_windows_name_prefixed(self):
        # CON/PRN/AUX/NUL are reserved on Windows — must not collide.
        out = sanitize_filename("CON")
        assert out.upper() != "CON"


# ---------------------------------------------------------------------------
# Clean export
# ---------------------------------------------------------------------------

class TestCleanExport:
    def test_export_writes_body_only(self, store, tmp_path):
        note = _typed_note("Markdown export", "# Body\n\nClean output.")
        store.save_note(note)

        out = tmp_path / "obsidian"
        paths = export_all(store, out)
        assert len(paths) == 1

        text = paths[0].read_text(encoding="utf-8")
        assert not text.startswith("---")
        assert "nexanote:page" not in text
        assert "# Body" in text
        assert "Clean output." in text

    def test_filename_from_title(self, store, tmp_path):
        store.save_note(_typed_note("Réunion équipe", "notes"))
        out = tmp_path / "out"
        path = export_all(store, out)[0]
        assert path.name == "Réunion équipe.md"

    def test_invalid_chars_sanitized(self, store, tmp_path):
        store.save_note(_typed_note("bad/title:with*chars?", "x"))
        out = tmp_path / "out"
        path = export_all(store, out)[0]
        for forbidden in '/:*?<>"\\|':
            assert forbidden not in path.name
        assert path.name.endswith(".md")
        assert path.exists()

    def test_blank_title_uses_fallback(self, store, tmp_path):
        store.save_note(_typed_note("   ", "body"))
        out = tmp_path / "out"
        path = export_all(store, out)[0]
        assert path.name == "untitled.md"

    def test_duplicate_titles_handled(self, store, tmp_path):
        for i in range(3):
            store.save_note(_typed_note("Same", f"body {i}"))
        out = tmp_path / "out"
        names = sorted(p.name for p in export_all(store, out))
        assert names == ["Same (2).md", "Same (3).md", "Same.md"]

    def test_does_not_overwrite_existing_files(self, store, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        kept = out / "Notes.md"
        kept.write_text("preexisting content\n", encoding="utf-8")

        store.save_note(_typed_note("Notes", "exported content"))
        export_all(store, out)

        assert kept.read_text(encoding="utf-8") == "preexisting content\n"
        assert (out / "Notes (2).md").exists()
        assert "exported content" in (out / "Notes (2).md").read_text()

    def test_collisions_case_insensitive(self, store, tmp_path):
        # "notes.md" and "Notes.md" must not collide on case-insensitive FS.
        out = tmp_path / "out"
        out.mkdir()
        (out / "notes.md").write_text("x", encoding="utf-8")
        store.save_note(_typed_note("Notes", "exported"))
        names = {p.name for p in export_all(store, out)}
        assert "Notes.md" not in names

    def test_internal_storage_untouched(self, store, tmp_path):
        note = _typed_note("Internal", "body")
        store.save_note(note)
        internal = store.notes_dir / f"{note.id}.md"
        before = internal.read_bytes()

        export_all(store, tmp_path / "out")

        after = internal.read_bytes()
        assert before == after
        assert after.startswith(b"---\n"), "frontmatter must remain in internal file"

    def test_skips_soft_deleted(self, store, tmp_path):
        note = _typed_note("Trash", "x")
        note.soft_delete()
        store.save_note(note)
        assert export_all(store, tmp_path / "out") == []

    def test_export_plain_md_passthrough(self, store, tmp_path):
        # Plain MD already in notes_dir → exported as a clean copy too.
        _seed_plain(store, "PlainExisting", "# Plain\n\nbody\n")
        out = tmp_path / "out"
        paths = export_all(store, out)
        assert len(paths) == 1
        assert paths[0].name == "PlainExisting.md"
        text = paths[0].read_text(encoding="utf-8")
        assert "# Plain" in text
        assert not text.startswith("---")

    def test_multi_page_joined_with_blank_line(self, store, tmp_path):
        note = Note(title="Multi", note_type=NoteType.TYPED)
        note.add_page().typed_content = "page one"
        note.add_page().typed_content = "page two"
        store.save_note(note)

        path = export_all(store, tmp_path / "out")[0]
        text = path.read_text(encoding="utf-8")
        assert "page one" in text
        assert "page two" in text
        assert "nexanote:page" not in text  # internal markers stripped

    def test_export_note_returns_path(self, store, tmp_path):
        note = _typed_note("Single", "hello")
        store.save_note(note)
        loaded = store.get_note(note.id, load_pages=True)
        out = tmp_path / "single"
        path = export_note(loaded, out)
        assert path == out / "Single.md"
        assert path.read_text(encoding="utf-8") == "hello\n"


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    @pytest.fixture
    def client(self, tmp_path):
        db = FileNoteStore(tmp_path / "api_export_store")
        # Seed one managed + one plain note.
        managed = Note(title="Managed", note_type=NoteType.TYPED)
        managed.add_page().typed_content = "managed body"
        db.save_note(managed)
        (db.notes_dir / "Imported.md").write_text("plain body\n", encoding="utf-8")

        app = create_app(db)
        with TestClient(app) as c:
            yield c, db
        db.close()

    def test_endpoint_writes_clean_md(self, client, tmp_path):
        c, db = client
        target = tmp_path / "obsidian-vault"
        resp = c.post("/export/markdown", json={"target_dir": str(target)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["exported"] == 2
        assert data["target_dir"] == str(target)

        # Every produced file must be plain markdown (no frontmatter).
        for fpath in data["files"]:
            text = Path(fpath).read_text(encoding="utf-8")
            assert not text.startswith("---")

    def test_endpoint_default_target(self, client):
        c, db = client
        resp = c.post("/export/markdown", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_dir"].endswith("export")
        assert data["exported"] == 2

    def test_endpoint_does_not_touch_internal_storage(self, client, tmp_path):
        c, db = client
        before = {
            p.name: p.read_bytes()
            for p in db.notes_dir.glob("*.md")
        }
        c.post("/export/markdown", json={"target_dir": str(tmp_path / "out")})
        after = {
            p.name: p.read_bytes()
            for p in db.notes_dir.glob("*.md")
        }
        assert before == after
