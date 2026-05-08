"""
NexaNote — Legacy/manual Markdown ignore tests.

EN: Cover the duplicate-creation bug where the WebDAV sync engine kept
    re-importing legacy `.md` files (manually dropped into the WebDAV
    folder, no NexaNote frontmatter) on every pull. The fix records each
    such file in a per-data-dir registry so subsequent syncs short-circuit
    instead of producing fresh duplicates.

FR: Couvre le bug où des .md hérités (ajoutés manuellement, sans
    frontmatter NexaNote) étaient réimportés à chaque pull. La correction
    enregistre ces chemins dans un registre par data_dir pour que les
    sync suivantes les ignorent immédiatement.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexanote.models.note import Note, Notebook, NoteType, SyncStatus
from nexanote.storage import FileNoteStore
from nexanote.sync.client import (
    NexaNoteSyncEngine,
    SyncConfig,
    SyncReport,
    _is_legacy_remote_id,
    _remote_path,
)
from nexanote.sync.sync_state import SYNC_STATE_FILENAME, SyncState


# ---------------------------------------------------------------------------
# Stub WebDAV client — feeds canned responses to ``NexaNoteSyncEngine``.
#
# We can't reproduce the duplicate-creation bug against the real WsgiDAV
# fixture because the production WebDAV provider only lists notes whose
# `notebook_id` matches the requested notebook — synthesised plain-MD
# files have `notebook_id=None` and therefore stay invisible. The bug
# report concerns clients pointed at WebDAV folders curated by other
# tools (Obsidian, Synology, hand-edited NAS folders) where bare `.md`
# files do show up at the notebook level. The stub mirrors that shape.
# ---------------------------------------------------------------------------


class StubWebDAVClient:
    """
    EN: Minimal in-memory replacement for ``WebDAVClient`` that returns
        canned ``list_notebooks`` / ``list_notes`` / ``get_note_meta`` /
        ``get_ink_page`` results from a dictionary handed in by the test.
    FR: Remplaçant en mémoire pour ``WebDAVClient`` — réponses canned.
    """

    def __init__(self, layout: dict) -> None:
        # layout = {nb_slug: {note_slug: meta_or_None}}
        self.layout = layout
        self.put_calls: list[tuple] = []

    def ping(self) -> bool:
        return True

    def list_notebooks(self) -> list[dict]:
        return [
            {"name": nb_slug, "is_collection": True, "href": f"/{nb_slug}/"}
            for nb_slug in self.layout
        ]

    def list_notes(self, notebook_slug: str) -> list[dict]:
        notes = self.layout.get(notebook_slug, {})
        return [
            {"name": note_slug, "is_collection": True, "href": f"/{notebook_slug}/{note_slug}/"}
            for note_slug in notes
        ]

    def get_note_meta(
        self, notebook_slug: str, note_slug: str
    ) -> Optional[dict]:
        return self.layout.get(notebook_slug, {}).get(note_slug)

    def get_ink_page(
        self, notebook_slug: str, note_slug: str, page_num: int
    ) -> Optional[dict]:
        return None

    # The push path isn't exercised by the legacy-ignore tests, but we
    # stub it harmlessly so engine.sync() can complete cleanly.
    def create_notebook_dir(self, notebook_slug: str) -> bool:
        return True

    def create_note_dir(self, notebook_slug: str, note_slug: str) -> bool:
        return True

    def put_note_meta(self, *args, **kwargs):
        self.put_calls.append(("meta", args, kwargs))
        return True, None

    def put_ink_page(self, *args, **kwargs):
        self.put_calls.append(("ink", args, kwargs))
        return True, None


def _make_engine_with_stub(client_dir: Path, layout: dict) -> tuple[
    NexaNoteSyncEngine, StubWebDAVClient
]:
    local_db = FileNoteStore(client_dir)
    engine = NexaNoteSyncEngine(
        local_db,
        SyncConfig(
            server_url="http://stub.invalid/",
            username="u",
            password="p",
            timeout_seconds=1,
        ),
    )
    stub = StubWebDAVClient(layout)
    engine.client = stub
    return engine, stub


def _legacy_meta(note_id: str, title: str, body: str = "raw") -> dict:
    """A note.json payload as the WebDAV provider would synthesise it."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": note_id,
        "title": title,
        "type": "typed",
        "tags": [],
        "is_pinned": False,
        "created_at": now,
        "updated_at": now,
        "pages": [
            {"page_number": 1, "template": "blank", "typed_content": body},
        ],
    }


def _local_note_count(db: FileNoteStore) -> int:
    return len(db.list_notes(include_archived=True))


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


class TestLegacyIdHelper:
    def test_md_prefix_is_legacy(self):
        assert _is_legacy_remote_id("md.aGVsbG8gd29ybGQ")

    def test_uuid_is_not_legacy(self):
        assert not _is_legacy_remote_id("abc12345-6789-4abc-9def-0123456789ab")

    def test_empty_id_is_legacy(self):
        # No id is the worst case — clearly not safely mappable.
        assert _is_legacy_remote_id(None)
        assert _is_legacy_remote_id("")

    def test_remote_path_concatenates(self):
        assert _remote_path("uncategorized", "foo__md.aGVs") == (
            "uncategorized/foo__md.aGVs"
        )


class TestSyncStateRoundtrip:
    def test_roundtrip_persists_adopted_and_ignored(self, tmp_path):
        state = SyncState.load(tmp_path)
        state.mark_adopted("nb/foo__abcd1234", "abcd1234-uuid")
        state.mark_ignored("nb/legacy__md.aGVs", "no NexaNote ID")
        state.save()

        reloaded = SyncState.load(tmp_path)
        assert reloaded.get_adopted_local_id("nb/foo__abcd1234") == "abcd1234-uuid"
        assert reloaded.is_ignored("nb/legacy__md.aGVs")
        assert reloaded.count_ignored() == 1
        assert (
            reloaded.get_ignored_reason("nb/legacy__md.aGVs")
            == "no NexaNote ID"
        )

    def test_mark_adopted_clears_previous_ignore(self, tmp_path):
        state = SyncState.load(tmp_path)
        state.mark_ignored("nb/foo", "...")
        state.mark_adopted("nb/foo", "uuid")
        assert not state.is_ignored("nb/foo")
        assert state.count_ignored() == 0

    def test_load_handles_missing_file(self, tmp_path):
        state = SyncState.load(tmp_path / "nonexistent")
        assert state.adopted == {}
        assert state.ignored == {}

    def test_load_handles_corrupt_file(self, tmp_path):
        (tmp_path / SYNC_STATE_FILENAME).write_text("{ not json", encoding="utf-8")
        state = SyncState.load(tmp_path)
        # Corruption is logged but never fatal.
        assert state.adopted == {}
        assert state.ignored == {}


# ---------------------------------------------------------------------------
# End-to-end pull tests
# ---------------------------------------------------------------------------


class TestPullIgnoresLegacyManualMarkdown:
    """
    EN: A remote ``note.json`` whose id was synthesised from a plain
        Markdown file (id begins with ``md.``) must be recorded in the
        ignore registry on first encounter and skipped on every later
        sync. This prevents the duplicate-creation loop the fix targets.
    FR: Une note distante au id synthétique (``md.``) doit être marquée
        ignorée dès le premier sync, puis sautée sans bruit ensuite.
    """

    def test_legacy_md_imported_zero_times(self, tmp_path):
        layout = {
            "uncategorized": {
                "recipe__md.UmVjaXBl": _legacy_meta(
                    "md.UmVjaXBl", "Recipe", "# My recipe\n\nRaw text.\n"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(
            tmp_path / "client_legacy", layout
        )

        report = engine.sync()

        assert report.success(), f"sync errors: {report.errors}"
        # Legacy file must be ignored — never adopted.
        assert _local_note_count(engine.db) == 0
        assert report.notes_pulled == 0
        assert report.notes_ignored_legacy == 1, report.summary()

    def test_three_syncs_do_not_grow_local_count(self, tmp_path):
        layout = {
            "uncategorized": {
                "notes__md.Tm90ZXM": _legacy_meta(
                    "md.Tm90ZXM", "Notes", "Free-form Obsidian note.\n"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(
            tmp_path / "client_thrice", layout
        )

        counts: list[int] = []
        ignore_counts: list[int] = []
        for _ in range(3):
            report = engine.sync()
            assert report.success(), f"sync errors: {report.errors}"
            counts.append(_local_note_count(engine.db))
            ignore_counts.append(report.notes_ignored_legacy)

        assert counts == [0, 0, 0], (
            f"legacy files must not accumulate; got {counts}"
        )
        # Each pull still reports the legacy file (so users can see it's
        # still on the server) but never grows the local note count.
        assert all(n >= 1 for n in ignore_counts), ignore_counts

    def test_no_weird_md_title_artifacts(self, tmp_path):
        """
        EN: The fix must skip the import path entirely for legacy ids,
            so no slug-derived `Foo__Md.…` titles can leak into the local
            store. A handful of would-be-bogus ids/titles are pulled and
            we assert nothing landed locally.
        """
        layout = {
            "uncategorized": {
                "todolist__md.VG9kbw": _legacy_meta(
                    "md.VG9kbw", "TodoList", "- buy milk\n"
                ),
                "__md.X19fX19f": _legacy_meta(
                    "md.X19fX19f", ".md", "no title at all\n"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(
            tmp_path / "client_titles", layout
        )

        report = engine.sync()
        assert report.success(), report.errors
        assert report.notes_ignored_legacy == 2

        # No legacy notes leaked into the local store under ANY title.
        local_notes = engine.db.list_notes(include_archived=True)
        assert local_notes == [], f"unexpected local notes: {local_notes}"

    def test_ignored_paths_persist_to_disk(self, tmp_path):
        layout = {
            "uncategorized": {
                "old__md.T2xk": _legacy_meta("md.T2xk", "Old"),
            }
        }
        client_dir = tmp_path / "client_persist"
        engine, _ = _make_engine_with_stub(client_dir, layout)
        engine.sync()

        # A second engine reading the same data dir sees the registry, so
        # the next sync session knows to skip this remote_path immediately.
        reloaded = SyncState.load(client_dir)
        assert reloaded.count_ignored() >= 1
        ignored = reloaded.all_ignored_paths()
        assert "uncategorized/old__md.T2xk" in ignored, ignored

    def test_no_id_means_ignored(self, tmp_path):
        """A remote payload with no id at all is the worst-case legacy."""
        layout = {
            "uncategorized": {
                "noid__deadbeef": {
                    # No "id" key at all.
                    "title": "ID-less",
                    "type": "typed",
                    "tags": [],
                    "is_pinned": False,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "pages": [],
                },
            }
        }
        engine, _ = _make_engine_with_stub(tmp_path / "client_noid", layout)
        report = engine.sync()

        assert report.success(), report.errors
        assert report.notes_ignored_legacy == 1
        assert _local_note_count(engine.db) == 0


# ---------------------------------------------------------------------------
# Adoption flow stays intact
# ---------------------------------------------------------------------------


class TestPullAdoptsValidNexaNoteFrontmatter:
    """
    EN: The fix must NOT break the normal pull path. A note with valid
        NexaNote frontmatter (real UUID id) is still adopted on first sync
        and matched by id on subsequent syncs.
    """

    def test_real_note_imports_once_and_only_once(self, tmp_path):
        real_id = "abc12345-6789-4abc-9def-0123456789ab"
        layout = {
            "recettes__abc12345": {
                f"soupe__{real_id[:8]}": _legacy_meta(
                    real_id, "Soupe à l'oignon", "Étape 1…"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(
            tmp_path / "client_real", layout
        )

        first = engine.sync()
        assert first.success(), first.errors
        assert first.notes_pulled == 1
        assert _local_note_count(engine.db) == 1
        assert first.notes_ignored_legacy == 0

        # Second sync against the unchanged remote is a no-op for pull,
        # adoption mapping is reused.
        second = engine.sync()
        assert second.success(), second.errors
        assert second.notes_pulled == 0
        assert _local_note_count(engine.db) == 1

    def test_real_note_alongside_legacy_md(self, tmp_path):
        """
        EN: Mixed remote: a real frontmatter note adopts; a legacy plain-md
            sibling is ignored. Re-syncing stays steady on both fronts.
        """
        real_id = "fedcba98-7654-4abc-9def-1234567890ab"
        layout = {
            "mixte__fedcba98": {
                f"vraie-note__{real_id[:8]}": _legacy_meta(
                    real_id, "Vraie note", "real content"
                ),
                "manual__md.TWFudWFs": _legacy_meta(
                    "md.TWFudWFs", "Manual", "Hand-edited file\n"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(
            tmp_path / "client_mixed", layout
        )

        report = engine.sync()
        assert report.success(), report.errors

        local = engine.db.get_note(real_id, load_pages=True)
        assert local is not None
        assert local.title == "Vraie note"
        assert report.notes_ignored_legacy == 1

        # Re-running stays steady on both fronts.
        second = engine.sync()
        assert second.success(), second.errors
        assert second.notes_pulled == 0
        assert _local_note_count(engine.db) == 1
        assert second.notes_ignored_legacy == 1


# ---------------------------------------------------------------------------
# remote_path adoption fallback
# ---------------------------------------------------------------------------


class TestRemotePathAdoptionFallback:
    """
    EN: When a remote note's id changes between syncs but the remote path
        stays the same, the engine should match via remote_path so the
        local note isn't duplicated. (This is the "match by remote_path
        after id miss" branch.)
    """

    def test_remote_path_match_avoids_duplicate(self, tmp_path):
        # Pre-seed the registry as if we adopted this remote_path before
        # under a different id. The remote returns a fresh-but-legacy id.
        client_dir = tmp_path / "client_remote_path"
        local_db = FileNoteStore(client_dir)
        # Adopted note exists locally with a real UUID.
        local_id = "11111111-2222-4333-9444-555566667777"
        seeded = Note(
            id=local_id,
            title="Adopted",
            note_type=NoteType.TYPED,
            sync_status=SyncStatus.SYNCED,
        )
        seeded.add_page().typed_content = "old"
        local_db.save_note(seeded)

        state = SyncState.load(client_dir)
        state.mark_adopted(
            "uncategorized/adopted__11111111", local_id
        )
        state.save()

        # Server now returns a different (legacy-shaped) id at the same path.
        layout = {
            "uncategorized": {
                "adopted__11111111": _legacy_meta(
                    "md.QWRvcHRlZA", "Adopted", "new from remote"
                ),
            }
        }
        engine, _ = _make_engine_with_stub(client_dir, layout)
        report = engine.sync()

        assert report.success(), report.errors
        # The local note still exists — wasn't duplicated.
        assert _local_note_count(engine.db) == 1
        # And no new ignore marker was added (we have a local mapping).
        assert engine.sync_state.count_ignored() == 0


# ---------------------------------------------------------------------------
# Diagnostics surface
# ---------------------------------------------------------------------------


class TestReportSurfaces:
    def test_summary_mentions_ignored_count(self):
        report = SyncReport()
        report.notes_ignored_legacy = 3
        report.finish()
        # Summary should call out the ignored legacy count.
        assert "3" in report.summary()
        assert (
            "ignor" in report.summary().lower()
            or "héritées" in report.summary()
        )

    def test_summary_omits_ignored_when_zero(self):
        report = SyncReport()
        report.finish()
        assert "ignor" not in report.summary().lower()
        assert "héritées" not in report.summary()
