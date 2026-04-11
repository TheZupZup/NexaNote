"""
Tests NexaNote — WebDAV provider et résolution de conflits
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point, SyncStatus
from nexanote.storage.database import NexaNoteDB
from nexanote.sync.conflict import ConflictResolver, ConflictStrategy
from nexanote.sync.webdav_provider import (
    NexaNoteDAVProvider,
    RootCollection,
    NotebookCollection,
    NoteCollection,
    _slugify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    db = NexaNoteDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def populated_db(tmp_db):
    """DB avec un carnet et deux notes."""
    nb = Notebook(name="Cours de maths", color="#3b82f6")
    tmp_db.save_notebook(nb)

    note1 = Note(notebook_id=nb.id, title="Algèbre linéaire", note_type=NoteType.TYPED)
    page1 = note1.add_page(template="lined")
    page1.typed_content = "Les matrices sont des tableaux de nombres."
    tmp_db.save_note(note1)

    note2 = Note(notebook_id=nb.id, title="Calcul différentiel", note_type=NoteType.HANDWRITTEN)
    page2 = note2.add_page(template="blank")
    stroke = InkStroke(
        color="#ff0000", width=2.0, tool="pen",
        points=[Point(10, 20, 0.5), Point(50, 80, 0.8), Point(90, 40, 0.6)]
    )
    page2.add_stroke(stroke)
    tmp_db.save_note(note2)

    return tmp_db, nb, note1, note2


# ---------------------------------------------------------------------------
# Tests slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("Mon carnet") == "mon-carnet"

    def test_accents_removed(self):
        result = _slugify("Algèbre linéaire")
        assert " " not in result
        assert result  # Non vide

    def test_empty(self):
        assert _slugify("") == "sans-titre"

    def test_special_chars(self):
        result = _slugify("Note #1 / Test!")
        assert "/" not in result
        assert "#" not in result


# ---------------------------------------------------------------------------
# Tests WebDAV Provider
# ---------------------------------------------------------------------------

def _make_environ(provider):
    """
    WsgiDAV exige que environ contienne 'wsgidav.provider'.
    Ce helper crée un environ minimal pour les tests unitaires.
    """
    return {
        "wsgidav.provider": provider,
        "wsgidav.config": {},
        "REQUEST_METHOD": "GET",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8765",
        "wsgi.url_scheme": "http",
    }


class TestNexaNoteDAVProvider:

    def test_root_lists_notebooks(self, populated_db):
        db, nb, *_ = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        root = provider.get_resource_inst("/", environ)
        assert root is not None
        members = root.get_member_names()
        assert len(members) == 1
        assert nb.id[:8] in members[0]

    def test_notebook_collection_lists_notes(self, populated_db):
        db, nb, note1, note2 = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        root = provider.get_resource_inst("/", environ)
        nb_name = root.get_member_names()[0]

        nb_col = provider.get_resource_inst(f"/{nb_name}", environ)
        assert nb_col is not None
        assert isinstance(nb_col, NotebookCollection)

        note_names = nb_col.get_member_names()
        assert len(note_names) == 2

    def test_note_collection_has_correct_files(self, populated_db):
        db, nb, note1, note2 = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        root = provider.get_resource_inst("/", environ)
        nb_name = root.get_member_names()[0]
        nb_col = provider.get_resource_inst(f"/{nb_name}", environ)
        note_name = nb_col.get_member_names()[0]

        note_col = provider.get_resource_inst(f"/{nb_name}/{note_name}", environ)
        assert note_col is not None
        assert isinstance(note_col, NoteCollection)

        files = note_col.get_member_names()
        assert "note.json" in files
        assert "page_1.ink" in files

    def test_note_json_readable(self, populated_db):
        import json
        db, nb, note1, note2 = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        root = provider.get_resource_inst("/", environ)
        nb_name = root.get_member_names()[0]
        nb_col = provider.get_resource_inst(f"/{nb_name}", environ)

        note_name = next(n for n in nb_col.get_member_names() if "alg" in n)
        json_file = provider.get_resource_inst(f"/{nb_name}/{note_name}/note.json", environ)

        assert json_file is not None
        content = json_file.get_content().read()
        data = json.loads(content)

        assert data["title"] == "Algèbre linéaire"
        assert data["type"] == "typed"
        assert len(data["pages"]) == 1
        assert "matrices" in data["pages"][0]["typed_content"]

    def test_ink_file_readable(self, populated_db):
        import json
        db, nb, note1, note2 = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)

        root = provider.get_resource_inst("/", environ)
        nb_name = root.get_member_names()[0]
        nb_col = provider.get_resource_inst(f"/{nb_name}", environ)

        note_name = next(n for n in nb_col.get_member_names() if "calcul" in n)
        ink_file = provider.get_resource_inst(f"/{nb_name}/{note_name}/page_1.ink", environ)

        assert ink_file is not None
        content = json.loads(ink_file.get_content().read())

        assert content["page_number"] == 1
        assert len(content["strokes"]) == 1
        assert content["strokes"][0]["color"] == "#ff0000"
        assert len(content["strokes"][0]["points"]) == 3

    def test_nonexistent_path_returns_none(self, populated_db):
        db, *_ = populated_db
        provider = NexaNoteDAVProvider(db)
        environ = _make_environ(provider)
        result = provider.get_resource_inst("/inexistant/chemin", environ)
        assert result is None


# ---------------------------------------------------------------------------
# Tests ConflictResolver
# ---------------------------------------------------------------------------

def _make_note_with_stroke(title: str, color: str, updated_offset_seconds: int = 0) -> Note:
    """Crée une note avec un stroke pour les tests de conflit."""
    note = Note(title=title, note_type=NoteType.HANDWRITTEN)
    page = note.add_page()
    stroke = InkStroke(
        color=color,
        points=[Point(10, 20), Point(30, 40)],
    )
    page.add_stroke(stroke)
    if updated_offset_seconds:
        note.updated_at = datetime.now(timezone.utc) + timedelta(seconds=updated_offset_seconds)
    # Réinitialiser le sync_status après construction — add_stroke le met à MODIFIED
    note.sync_status = SyncStatus.LOCAL_ONLY
    return note


class TestConflictResolver:

    def test_no_conflict_same_timestamp(self):
        note = _make_note_with_stroke("Note", "#000")
        local = note
        remote = note  # Même objet = même timestamp

        resolver = ConflictResolver()
        result = resolver.resolve(local, remote)
        assert not result.had_conflict()

    def test_last_write_wins_local_newer(self):
        local = _make_note_with_stroke("Note", "#000", updated_offset_seconds=10)
        remote = _make_note_with_stroke("Note", "#000", updated_offset_seconds=0)
        local.id = remote.id = "same-id"

        resolver = ConflictResolver(strategy=ConflictStrategy.LAST_WRITE_WINS)
        result = resolver.resolve(local, remote)

        assert result.winner is local
        assert result.conflict_copy is None

    def test_last_write_wins_remote_newer(self):
        local = _make_note_with_stroke("Note", "#000", updated_offset_seconds=0)
        remote = _make_note_with_stroke("Note", "#000", updated_offset_seconds=10)
        local.id = remote.id = "same-id"

        resolver = ConflictResolver(strategy=ConflictStrategy.LAST_WRITE_WINS)
        result = resolver.resolve(local, remote)

        assert result.winner is remote

    def test_keep_both_creates_conflict_copy(self):
        local = _make_note_with_stroke("Ma note", "#000", updated_offset_seconds=5)
        remote = _make_note_with_stroke("Ma note", "#000", updated_offset_seconds=0)
        local.id = remote.id = "same-id"

        resolver = ConflictResolver(strategy=ConflictStrategy.KEEP_BOTH)
        result = resolver.resolve(local, remote)

        assert result.winner is remote
        assert result.conflict_copy is not None
        assert "conflit" in result.conflict_copy.title.lower()
        assert result.conflict_copy.id != result.winner.id  # Copie indépendante

    def test_merge_strokes_combines_unique_strokes(self):
        """
        Local a stroke rouge, remote a stroke bleu.
        Après fusion, la note doit avoir les deux.
        """
        import copy

        base = Note(title="Note", note_type=NoteType.HANDWRITTEN)
        base.add_page()

        local = copy.deepcopy(base)
        remote = copy.deepcopy(base)
        remote.id = local.id  # Même note

        # Stroke sur local seulement
        red_stroke = InkStroke(color="#ff0000", points=[Point(0,0), Point(10,10)])
        local.pages[0].add_stroke(red_stroke)
        local.updated_at = datetime.now(timezone.utc) + timedelta(seconds=5)

        # Stroke sur remote seulement
        blue_stroke = InkStroke(color="#0000ff", points=[Point(20,20), Point(30,30)])
        remote.pages[0].add_stroke(blue_stroke)

        resolver = ConflictResolver(strategy=ConflictStrategy.MERGE_STROKES)
        result = resolver.resolve(local, remote)

        colors = {s.color for s in result.winner.pages[0].strokes}
        assert "#ff0000" in colors
        assert "#0000ff" in colors
        assert result.strokes_merged == 1

    def test_merge_strokes_no_duplicate(self):
        """Un stroke présent des deux côtés ne doit pas être dupliqué."""
        import copy

        base = Note(title="Note", note_type=NoteType.HANDWRITTEN)
        base.add_page()
        shared_stroke = InkStroke(color="#000", points=[Point(0,0), Point(10,10)])
        base.pages[0].add_stroke(shared_stroke)

        local = copy.deepcopy(base)
        remote = copy.deepcopy(base)
        remote.id = local.id
        remote.updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)

        resolver = ConflictResolver(strategy=ConflictStrategy.MERGE_STROKES)
        result = resolver.resolve(local, remote)

        assert len(result.winner.pages[0].strokes) == 1  # Pas de doublon
        assert result.strokes_merged == 0

    def test_winner_sync_status_is_synced(self):
        local = _make_note_with_stroke("Note", "#000", updated_offset_seconds=5)
        remote = _make_note_with_stroke("Note", "#000", updated_offset_seconds=0)
        local.id = remote.id = "same-id"

        resolver = ConflictResolver()
        result = resolver.resolve(local, remote)

        assert result.winner.sync_status == SyncStatus.SYNCED
