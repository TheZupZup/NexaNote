"""
NexaNote — Sync reliability & diagnostics tests.

EN: Covers the reliability layer added around the WebDAV sync engine:
      * a ``SyncPlan`` that records intent (push / pull / ignore / conflict);
      * a dry-run mode that builds the plan but writes nothing;
      * a sanitized ``<data_dir>/sync_logs/latest.json`` diagnostic log;
      * conflict safety — both-changed notes are detected and never silently
        overwritten;
      * idempotent state — a failed sync leaves the sync-state file intact.

FR: Couvre la couche de fiabilité ajoutée autour du moteur de sync WebDAV :
    plan de sync, mode dry-run, journal assaini, sécurité des conflits, et
    état non corrompu après un échec.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexanote.models.note import Note, Notebook, NoteType, SyncStatus
from nexanote.storage import FileNoteStore
from nexanote.sync.client import NexaNoteSyncEngine, SyncConfig, SyncReport
from nexanote.sync.plan import SyncPlan
from nexanote.sync.sync_log import (
    SYNC_LOG_FILENAME,
    build_sync_log,
    read_sync_log,
    sanitize_error,
    sync_log_path,
)
from nexanote.sync.sync_state import SYNC_STATE_FILENAME, SyncState


# ---------------------------------------------------------------------------
# Stub WebDAV client
# ---------------------------------------------------------------------------


class StubWebDAVClient:
    """
    EN: In-memory stand-in for ``WebDAVClient``. Returns canned remote
        layouts and records every PUT so tests can assert that a dry-run
        never touched the server.
    FR: Remplaçant en mémoire pour ``WebDAVClient``, avec suivi des PUT.
    """

    def __init__(self, layout: Optional[dict] = None) -> None:
        # layout = {nb_slug: {note_slug: meta_dict}}
        self.layout = layout or {}
        self.put_calls: list[tuple] = []
        self.mkcol_calls: list[str] = []
        self.online = True
        self.put_result: tuple = (True, None)

    def ping(self) -> bool:
        return self.online

    def list_notebooks(self) -> list[dict]:
        return [
            {"name": nb, "is_collection": True, "href": f"/{nb}/"}
            for nb in self.layout
        ]

    def list_notes(self, notebook_slug: str) -> list[dict]:
        return [
            {"name": note, "is_collection": True, "href": f"/{notebook_slug}/{note}/"}
            for note in self.layout.get(notebook_slug, {})
        ]

    def get_note_meta(self, notebook_slug: str, note_slug: str) -> Optional[dict]:
        return self.layout.get(notebook_slug, {}).get(note_slug)

    def get_ink_page(self, notebook_slug: str, note_slug: str, page_num: int):
        return None

    def create_notebook_dir(self, notebook_slug: str) -> bool:
        self.mkcol_calls.append(notebook_slug)
        return True

    def create_note_dir(self, notebook_slug: str, note_slug: str) -> bool:
        self.mkcol_calls.append(f"{notebook_slug}/{note_slug}")
        return True

    def put_note_meta(self, *args, **kwargs):
        self.put_calls.append(("meta", args, kwargs))
        return self.put_result

    def put_ink_page(self, *args, **kwargs):
        self.put_calls.append(("ink", args, kwargs))
        return self.put_result


def _meta(note_id: str, title: str, body: str, updated_iso: str) -> dict:
    """A note.json payload as the WebDAV provider would synthesise it."""
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


def _make_engine(data_dir: Path, layout: dict, *, dry_run: bool = False):
    db = FileNoteStore(data_dir)
    engine = NexaNoteSyncEngine(
        db,
        SyncConfig(
            server_url="http://stub.invalid/",
            username="u",
            password="hunter2-secret",
            timeout_seconds=1,
        ),
        dry_run=dry_run,
    )
    stub = StubWebDAVClient(layout)
    engine.client = stub
    return engine, stub


def _note_count(db: FileNoteStore) -> int:
    return len(db.list_notes(include_archived=True))


REAL_ID = "abc12345-6789-4abc-9def-0123456789ab"
LEGACY_ID = "md.TGVnYWN5"  # "Legacy" base64-ish — the synthesised plain-md form


# ---------------------------------------------------------------------------
# 1. Dry-run does not modify files or state
# ---------------------------------------------------------------------------


class TestDryRun:
    def _layout_with_pull_and_ignore(self) -> dict:
        return {
            "carnet__01234567": {
                f"remote__{REAL_ID[:8]}": _meta(
                    REAL_ID, "From Server", "remote body", "2026-02-01T00:00:00+00:00"
                ),
                "legacy__md.TGVnYWN5": _meta(
                    LEGACY_ID, "Legacy", "hand-edited", "2026-02-01T00:00:00+00:00"
                ),
            }
        }

    def test_dry_run_writes_no_files_or_state(self, tmp_path):
        data_dir = tmp_path / "client"
        engine, stub = _make_engine(
            data_dir, self._layout_with_pull_and_ignore(), dry_run=True
        )

        # A local note that a real sync would push.
        local = Note(title="Pushable", note_type=NoteType.TYPED)
        local.add_page().typed_content = "to push"
        engine.db.save_note(local)
        assert engine.db.get_note(local.id).sync_status == SyncStatus.LOCAL_ONLY

        before = _note_count(engine.db)
        report = engine.sync()

        # Plan reflects what *would* happen.
        assert report.dry_run is True
        plan = report.plan
        assert plan is not None
        assert [n.note_id for n in plan.notes_to_push] == [local.id]
        assert REAL_ID in [n.note_id for n in plan.notes_to_pull]
        assert any(
            "legacy" in i.remote_path for i in plan.notes_to_ignore
        ), plan.to_dict()

        # Nothing was applied: no remote note adopted, push note untouched.
        assert _note_count(engine.db) == before
        assert engine.db.get_note(REAL_ID) is None
        assert engine.db.get_note(local.id).sync_status == SyncStatus.LOCAL_ONLY

        # No server writes, no sync-state file, no sync log file.
        assert stub.put_calls == []
        assert not (data_dir / SYNC_STATE_FILENAME).exists()
        assert not sync_log_path(data_dir).exists()

    def test_dry_run_leaves_existing_state_untouched(self, tmp_path):
        data_dir = tmp_path / "client_state"
        # Pre-seed a sync-state file and capture its bytes.
        seed = SyncState.load(data_dir)
        seed.mark_adopted("carnet__01234567/old__deadbeef", "deadbeef-uuid")
        seed.save()
        state_path = data_dir / SYNC_STATE_FILENAME
        original = state_path.read_bytes()

        engine, _ = _make_engine(
            data_dir, self._layout_with_pull_and_ignore(), dry_run=True
        )
        engine.sync()

        # Byte-for-byte identical — the dry-run never rewrote the registry.
        assert state_path.read_bytes() == original


# ---------------------------------------------------------------------------
# 2. Sync log is written after a real sync
# ---------------------------------------------------------------------------


class TestSyncLogWritten:
    def test_log_written_with_expected_shape(self, tmp_path):
        data_dir = tmp_path / "client"
        layout = {
            "carnet__01234567": {
                f"remote__{REAL_ID[:8]}": _meta(
                    REAL_ID, "From Server", "remote body", "2026-02-01T00:00:00+00:00"
                ),
            }
        }
        engine, _ = _make_engine(data_dir, layout)
        report = engine.sync()
        assert report.success(), report.errors

        log_file = sync_log_path(data_dir)
        assert log_file.exists()
        # Path resolves to <data_dir>/sync_logs/latest.json.
        assert log_file.parent.name == "sync_logs"
        assert log_file.name == SYNC_LOG_FILENAME

        payload = json.loads(log_file.read_text("utf-8"))
        assert payload["timestamp"]
        assert payload["duration_seconds"] >= 0
        assert payload["dry_run"] is False
        assert payload["success"] is True
        assert payload["counts"]["pulled"] == 1
        # The pulled note is listed by id/title.
        assert payload["pulled"][0]["id"] == REAL_ID
        assert payload["pulled"][0]["title"] == "From Server"

    def test_dry_run_writes_no_log(self, tmp_path):
        data_dir = tmp_path / "client_dry"
        engine, _ = _make_engine(data_dir, {}, dry_run=True)
        engine.sync()
        assert not sync_log_path(data_dir).exists()


# ---------------------------------------------------------------------------
# 3. Sync log excludes secrets and note body content
# ---------------------------------------------------------------------------


class TestSyncLogSanitization:
    def test_log_excludes_body_and_password(self, tmp_path):
        data_dir = tmp_path / "client"
        engine, stub = _make_engine(data_dir, {})

        # A local note whose body must never reach the log.
        local = Note(title="Secret Note", note_type=NoteType.TYPED)
        local.add_page().typed_content = "TOP-SECRET-BODY-CONTENT-12345"
        engine.db.save_note(local)

        report = engine.sync()
        assert report.success(), report.errors

        raw = sync_log_path(data_dir).read_text("utf-8")
        # The push is recorded by id + title only.
        assert "Secret Note" in raw
        assert local.id in raw
        # Never the body, never the configured password.
        assert "TOP-SECRET-BODY-CONTENT-12345" not in raw
        assert "hunter2-secret" not in raw

    def test_log_sanitizes_error_urls(self, tmp_path):
        data_dir = tmp_path / "client_err"
        engine, stub = _make_engine(data_dir, {})
        # Force the push to fail with a reason that embeds a server URL.
        stub.put_result = (
            False,
            "WebDAV upload failed at https://nas.example.com:8765/dav/notes: 500",
        )
        local = Note(title="Will Fail", note_type=NoteType.TYPED)
        local.add_page().typed_content = "body"
        engine.db.save_note(local)

        report = engine.sync()
        assert not report.success()

        payload = read_sync_log(data_dir)
        assert payload is not None
        joined = json.dumps(payload)
        # The host/URL is scrubbed; the useful HTTP status survives.
        assert "nas.example.com" not in joined
        assert "<url>" in joined
        assert "500" in joined

    def test_sanitize_error_unit(self):
        assert sanitize_error("connect to https://host.tld:8765/dav failed") == (
            "connect to <url> failed"
        )
        assert "<redacted>" in sanitize_error("auth password=hunter2 rejected")
        assert "<redacted>" in sanitize_error("Authorization: Bearer abc.def.ghi")
        # HTTP status reasons carry no secret and must be preserved.
        assert sanitize_error("WebDAV upload failed: 401 Unauthorized") == (
            "WebDAV upload failed: 401 Unauthorized"
        )

    def test_build_sync_log_carries_no_body(self):
        """Even handed a report/plan directly, the builder emits no body."""
        report = SyncReport()
        report.notes_pushed = 1
        report.finish()
        plan = SyncPlan()
        plan.add_push("id-1", "A Title")
        payload = build_sync_log(report, plan)
        text = json.dumps(payload)
        assert "A Title" in text
        assert "id-1" in text
        # The plan/report never hold body, so none can appear.
        assert all("typed_content" not in str(v) for v in payload.values())


# ---------------------------------------------------------------------------
# 4. Ignored legacy files appear in diagnostics
# ---------------------------------------------------------------------------


class TestIgnoredDiagnostics:
    def test_ignored_legacy_in_report_plan_and_log(self, tmp_path):
        data_dir = tmp_path / "client"
        layout = {
            "carnet__01234567": {
                "legacy__md.TGVnYWN5": _meta(
                    LEGACY_ID, "Legacy", "hand-edited", "2026-02-01T00:00:00+00:00"
                ),
            }
        }
        engine, _ = _make_engine(data_dir, layout)
        report = engine.sync()
        assert report.success(), report.errors

        # Report counter.
        assert report.notes_ignored_legacy == 1
        # Plan lists the ignored remote path with a reason.
        ignored = report.plan.notes_to_ignore
        assert len(ignored) == 1
        assert ignored[0].remote_path == "carnet__01234567/legacy__md.TGVnYWN5"
        assert ignored[0].reason

        # The sanitized log surfaces the ignored remote path too.
        payload = read_sync_log(data_dir)
        assert payload["counts"]["ignored_legacy"] == 1
        assert payload["ignored"][0]["remote_path"] == (
            "carnet__01234567/legacy__md.TGVnYWN5"
        )
        # And the note was never adopted into the local store.
        assert _note_count(engine.db) == 0


# ---------------------------------------------------------------------------
# 5. Conflict is detected instead of silently overwritten
# ---------------------------------------------------------------------------


class TestConflictSafety:
    def test_both_changed_preserves_local_copy(self, tmp_path):
        data_dir = tmp_path / "client"
        layout = {
            "carnet__01234567": {
                f"remote__{REAL_ID[:8]}": _meta(
                    REAL_ID,
                    "Doc",
                    "REMOTE-EDIT",
                    "2026-06-01T00:00:00+00:00",  # remote is newer
                ),
            }
        }
        engine, _ = _make_engine(data_dir, layout)

        # Local note: same id, unsynced local edits, older timestamp.
        local = Note(id=REAL_ID, title="Doc", note_type=NoteType.TYPED)
        local.add_page().typed_content = "LOCAL-EDIT"
        local.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        local.sync_status = SyncStatus.MODIFIED
        engine.db.save_note(local)

        # Skip push so we exercise only the pull conflict path.
        engine._push = lambda report: None
        report = engine.sync()
        assert report.success(), report.errors

        # The conflict was detected and marked, not silently resolved away.
        assert report.conflicts_resolved == 1
        assert len(report.plan.conflicts) == 1
        conflict = report.plan.conflicts[0]
        assert conflict.note_id == REAL_ID
        assert conflict.preserved_both is True

        # Both versions survive: the local edit was NOT silently overwritten.
        bodies = []
        for n in engine.db.list_notes(include_archived=True):
            full = engine.db.get_note(n.id, load_pages=True)
            bodies.append(full.pages[0].typed_content if full.pages else "")
        assert any("LOCAL-EDIT" in b for b in bodies), bodies
        assert any("REMOTE-EDIT" in b for b in bodies), bodies

        # The diagnostic log records the conflict (ids/titles only).
        payload = read_sync_log(data_dir)
        assert payload["counts"]["conflicts"] == 1
        assert payload["conflicts"][0]["id"] == REAL_ID
        assert payload["conflicts"][0]["preserved_both"] is True

    def test_local_newer_does_not_spawn_redundant_copy(self, tmp_path):
        """When local already wins, no redundant conflict copy is created."""
        data_dir = tmp_path / "client_localnew"
        layout = {
            "carnet__01234567": {
                f"remote__{REAL_ID[:8]}": _meta(
                    REAL_ID, "Doc", "REMOTE-OLD", "2026-01-01T00:00:00+00:00"
                ),
            }
        }
        engine, _ = _make_engine(data_dir, layout)

        local = Note(id=REAL_ID, title="Doc", note_type=NoteType.TYPED)
        local.add_page().typed_content = "LOCAL-NEW"
        local.updated_at = datetime(2026, 6, 1, tzinfo=timezone.utc)  # newer
        local.sync_status = SyncStatus.MODIFIED
        engine.db.save_note(local)

        engine._push = lambda report: None
        report = engine.sync()
        assert report.success(), report.errors
        assert report.conflicts_resolved == 1
        # Local won outright — exactly one note, no spurious "(conflit …)" copy.
        assert _note_count(engine.db) == 1
        assert report.plan.conflicts[0].preserved_both is False


# ---------------------------------------------------------------------------
# 6. A failed sync does not corrupt sync state
# ---------------------------------------------------------------------------


class TestFailedSyncKeepsStateValid:
    def test_state_survives_push_exception(self, tmp_path):
        data_dir = tmp_path / "client"
        # Pre-seed a valid registry entry.
        seed = SyncState.load(data_dir)
        seed.mark_adopted("carnet__01234567/kept__deadbeef", "deadbeef-uuid")
        seed.save()

        layout = {
            "carnet__01234567": {
                "legacy__md.TGVnYWN5": _meta(
                    LEGACY_ID, "Legacy", "x", "2026-02-01T00:00:00+00:00"
                ),
            }
        }
        engine, _ = _make_engine(data_dir, layout)

        # Pull marks the legacy note ignored; then push blows up mid-sync.
        def _boom(report):
            raise RuntimeError("simulated push crash")

        engine._push = _boom
        report = engine.sync()

        # The crash was captured, not swallowed silently.
        assert not report.success()
        assert any("simulated push crash" in e for e in report.errors)

        # The state file is still valid JSON and fully reloadable.
        state_path = data_dir / SYNC_STATE_FILENAME
        assert state_path.exists()
        json.loads(state_path.read_text("utf-8"))  # must not raise

        reloaded = SyncState.load(data_dir)
        # Pre-existing adoption preserved …
        assert (
            reloaded.get_adopted_local_id("carnet__01234567/kept__deadbeef")
            == "deadbeef-uuid"
        )
        # … and the partial-run ignore decision was persisted by the finally
        # block, so the next sync skips it immediately.
        assert reloaded.is_ignored("carnet__01234567/legacy__md.TGVnYWN5")

    def test_unreachable_server_writes_failure_log_without_corruption(self, tmp_path):
        data_dir = tmp_path / "client_offline"
        engine, stub = _make_engine(data_dir, {})
        stub.online = False  # ping fails

        report = engine.sync()
        assert not report.success()

        # Even a failed connection produces a diagnostic log …
        payload = read_sync_log(data_dir)
        assert payload is not None
        assert payload["success"] is False
        assert payload["counts"]["errors"] >= 1
        # … with the server host scrubbed out of the error (it embeds a URL).
        text = json.dumps(payload)
        assert "stub.invalid" not in text
        assert "<url>" in text
        # … and no sync-state file is corrupted (none needed to be written).
        state_path = data_dir / SYNC_STATE_FILENAME
        if state_path.exists():
            json.loads(state_path.read_text("utf-8"))
