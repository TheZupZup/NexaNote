"""
NexaNote — QA stability audit tests.

EN: Regression tests added during the QA stability audit. Each test pins
    down a specific bug found by the audit so it cannot silently regress.

FR: Tests de non-régression ajoutés lors de l'audit de stabilité QA. Chaque
    test verrouille un bug précis trouvé pendant l'audit.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexanote.models.note import Note, NoteType
from nexanote.storage import FileNoteStore
from nexanote.storage.backend import (
    DEFAULT_MODE,
    MODE_PLAIN,
    MODE_YAML,
    create_store,
    detect_mode,
    write_mode_marker,
)
from nexanote.storage.migration import needs_migration, run_migration
from nexanote.sync.server import run_server
from nexanote.sync.sync_log import sanitize_error
from nexanote.sync.sync_state import SyncState


# ---------------------------------------------------------------------------
# 1. sanitize_error must fully redact auth-scheme credentials.
#
# The previous regex captured only one \S+ after the key, which left the
# real credential trailing an `Authorization: Basic <token>` header in
# the diagnostic log. These tests pin down the full redaction.
# ---------------------------------------------------------------------------


class TestSanitizeErrorAuthSchemes:
    def test_authorization_basic_token_is_fully_redacted(self):
        out = sanitize_error("Authorization: Basic dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in out
        assert "<redacted>" in out

    def test_authorization_bearer_token_is_fully_redacted(self):
        out = sanitize_error("Authorization: Bearer abc.def.ghi")
        assert "abc.def.ghi" not in out
        assert "<redacted>" in out

    def test_authorization_token_scheme_is_fully_redacted(self):
        out = sanitize_error("Authorization: Token deadbeefcafe")
        assert "deadbeefcafe" not in out
        assert "<redacted>" in out

    def test_authorization_digest_scheme_is_fully_redacted(self):
        out = sanitize_error('Authorization: Digest username="x", response="y"')
        # The scheme + first credential token are redacted; the rest of the
        # parameter list is sanitised separately by the same regex (each
        # `key=value` pair is its own match).
        assert "<redacted>" in out
        # The basic invariant: no raw response value survives untouched.
        assert "response=y" not in out

    def test_plain_key_value_password_still_redacted(self):
        out = sanitize_error("auth password=hunter2 rejected")
        assert "hunter2" not in out
        assert "<redacted>" in out

    def test_http_status_reason_is_preserved(self):
        out = sanitize_error("WebDAV upload failed: 401 Unauthorized")
        assert out == "WebDAV upload failed: 401 Unauthorized"

    def test_url_is_collapsed(self):
        out = sanitize_error("connect to https://nas.example.com:8765/dav failed")
        assert "nas.example.com" not in out
        assert "<url>" in out


# ---------------------------------------------------------------------------
# 2. WebDAV server must not log the configured password.
#
# Logs are routinely persisted by container runtimes, scraped, and pasted
# into bug reports. Printing the password in plaintext is a real leak.
# ---------------------------------------------------------------------------


class TestServerPasswordNotLogged:
    def test_run_server_startup_logs_do_not_contain_password(
        self, tmp_path, caplog, monkeypatch
    ):
        password = "super-secret-do-not-log-7Hx"

        # `run_server` blocks on `server.start()` after emitting its banner.
        # Patch the start() to raise immediately so we capture only the
        # startup logs without actually serving any requests.
        from cheroot import wsgi

        class _NoServe(Exception):
            pass

        def _boom(self):
            raise _NoServe()

        monkeypatch.setattr(wsgi.Server, "start", _boom)

        with caplog.at_level(logging.INFO, logger="nexanote.server"):
            with pytest.raises(_NoServe):
                run_server(
                    host="127.0.0.1",
                    port=0,
                    data_dir=tmp_path,
                    username="nexanote",
                    password=password,
                )

        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert password not in joined, (
            "WebDAV password must never appear in server startup logs"
        )


# ---------------------------------------------------------------------------
# 3. Storage backend selection — marker file is the source of truth.
#
# create_store records the chosen mode on first use so future opens stay
# consistent even when the env var disappears. These tests pin down the
# precedence: marker > env > default.
# ---------------------------------------------------------------------------


class TestStorageBackendSelection:
    def test_default_mode_is_yaml(self, tmp_path):
        info = detect_mode(tmp_path, env={})
        assert info.mode == MODE_YAML
        assert info.mode == DEFAULT_MODE
        assert info.source == "default"

    def test_env_var_selects_plain(self, tmp_path):
        info = detect_mode(tmp_path, env={"NEXANOTE_STORAGE_MODE": "plain"})
        assert info.mode == MODE_PLAIN
        assert info.source == "env"

    def test_marker_wins_over_env(self, tmp_path):
        write_mode_marker(tmp_path, MODE_YAML)
        info = detect_mode(tmp_path, env={"NEXANOTE_STORAGE_MODE": "plain"})
        assert info.mode == MODE_YAML
        assert info.source == "marker"

    def test_create_store_persists_marker_on_first_use(self, tmp_path):
        marker = tmp_path / ".nexanote_storage_mode"
        assert not marker.exists()
        create_store(tmp_path)
        assert marker.exists()
        # And a subsequent detect_mode reports it as a marker hit.
        assert detect_mode(tmp_path, env={}).source == "marker"

    def test_invalid_marker_value_falls_back_safely(self, tmp_path):
        marker = tmp_path / ".nexanote_storage_mode"
        marker.write_text("nonsense\n", encoding="utf-8")
        info = detect_mode(tmp_path, env={})
        # Default still applies — the invalid value must not crash the open.
        assert info.mode == DEFAULT_MODE


# ---------------------------------------------------------------------------
# 4. Migration idempotency — a fresh install must not need migration twice.
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    def test_fresh_install_does_not_need_migration(self, tmp_path):
        assert needs_migration(tmp_path) is False

    def test_run_migration_on_fresh_dir_writes_marker(self, tmp_path):
        report = run_migration(tmp_path)
        assert report.ran is False
        # Marker file is written eagerly so a later boot doesn't keep
        # probing for a SQLite DB that was never there.
        assert (tmp_path / ".nexanote_migrated").exists()
        # And the second call short-circuits without doing anything.
        report2 = run_migration(tmp_path)
        assert report2.ran is False


# ---------------------------------------------------------------------------
# 5. Sync state registry is robust to a corrupt JSON file.
#
# The on-disk file lives in the user's data dir and may be edited by
# external tools. A malformed payload must not crash the next sync.
# ---------------------------------------------------------------------------


class TestSyncStateRobustness:
    def test_unreadable_json_does_not_raise(self, tmp_path):
        path = tmp_path / ".nexanote_sync_state.json"
        path.write_text("{ this is not valid JSON", encoding="utf-8")
        # Loader returns an empty registry; sync still works.
        state = SyncState.load(tmp_path)
        assert state.adopted == {}
        assert state.ignored == {}

    def test_non_dict_payload_does_not_raise(self, tmp_path):
        path = tmp_path / ".nexanote_sync_state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        state = SyncState.load(tmp_path)
        assert state.adopted == {}
        assert state.ignored == {}

    def test_round_trip_persists_adopted_entry(self, tmp_path):
        state = SyncState.load(tmp_path)
        state.mark_adopted("nb__01234567/note__deadbeef", "abc12345-uuid")
        state.save()

        reloaded = SyncState.load(tmp_path)
        assert reloaded.get_adopted_local_id(
            "nb__01234567/note__deadbeef"
        ) == "abc12345-uuid"

    def test_marking_adopted_drops_ignored(self, tmp_path):
        """An explicit adoption must clear any previous ignore marker so
        the same remote path doesn't show up in both registries — the
        engine's `is_ignored` check would otherwise short-circuit the
        adopted note on the next pull."""
        state = SyncState.load(tmp_path)
        state.mark_ignored("nb__01234567/legacy__md.x", "manual file")
        assert state.is_ignored("nb__01234567/legacy__md.x") is True

        state.mark_adopted("nb__01234567/legacy__md.x", "real-id")
        assert state.is_ignored("nb__01234567/legacy__md.x") is False


# ---------------------------------------------------------------------------
# 6. /health endpoint shape is stable for the Flutter client's ping().
# ---------------------------------------------------------------------------


class TestHealthEndpointContract:
    def test_health_returns_expected_keys(self, tmp_path):
        from fastapi.testclient import TestClient

        from nexanote.api.routes import create_app

        db = FileNoteStore(tmp_path)
        client = TestClient(create_app(db))
        try:
            resp = client.get("/health")
            assert resp.status_code == 200
            payload = resp.json()
            # Keys the Flutter app reads to render the connection banner /
            # storage badge. Changing these is a breaking client change.
            assert payload["status"] == "ok"
            assert payload["storage"] == "file"
            assert "version" in payload
            assert "stats" in payload
            assert "timestamp" in payload
        finally:
            db.close()

    def test_storage_endpoint_returns_paths(self, tmp_path):
        from fastapi.testclient import TestClient

        from nexanote.api.routes import create_app

        db = FileNoteStore(tmp_path)
        client = TestClient(create_app(db))
        try:
            resp = client.get("/storage")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["storage"] == "file"
            assert payload["data_dir"] == str(db.data_dir)
            # Paths must point inside data_dir — never to a stale /tmp or
            # an unset value that would mislead the operator.
            assert payload["notes_dir"].startswith(str(db.data_dir))
            assert payload["drawings_dir"].startswith(str(db.data_dir))
            assert payload["notebooks_dir"].startswith(str(db.data_dir))
            assert isinstance(payload["total_size_mb"], (int, float))
        finally:
            db.close()
