# Changelog

All notable changes to NexaNote are documented in this file.

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased — Sync reliability & diagnostics

### New

- **Sync planning.** Each sync session now builds a `SyncPlan` recording
  what it will push, pull, ignore, and which notes are in conflict, plus
  any warnings. The plan is the single source the dry-run mode and the
  diagnostic log read from.

- **Dry-run mode.** `NexaNoteSyncEngine(db, config, dry_run=True)` — or
  `POST /sync/trigger?dry_run=true` — builds the plan without writing any
  note files, touching the sync-state registry, uploading to the remote,
  or writing a log. Use it to preview what a real sync would do.

- **Sanitized sync log.** Every real sync writes
  `<data_dir>/sync_logs/latest.json` (so `/data/sync_logs/latest.json` in
  the Docker image), readable via `GET /sync/log`. It records the
  timestamp, duration, counts, pushed/pulled note ids and titles, ignored
  legacy remote paths, conflicts, and sanitized errors. It never contains
  note body content, passwords, tokens, or server URLs — error strings are
  scrubbed and the plan/report only ever hold metadata.

- **Retry & backoff for transient failures.** Every WebDAV network
  operation (GET, PROPFIND, PUT, MKCOL) is now retried on transient
  conditions — timeouts, connection drops, and HTTP 429/502/503/504 —
  with small conservative defaults (3 attempts, 0.5s/1s/2s backoff).
  Auth failures (401/403) and 404 are never retried. Both the attempt
  budget and per-call timeout are configurable on `SyncConfig`
  (`max_attempts`, `backoff_seconds`, `timeout_seconds`).

- **Retryable sync reports.** When a sync fails for a transient reason the
  report (and `POST /sync/trigger`) carries `retryable: true`, a short
  `transient_reason`, and a suggested `next_retry_after_seconds`. The
  diagnostic log additionally records per-operation attempt counts so a
  flaky NAS/Cloudflare/mobile link is easy to spot — still with no body
  content, credentials, or server URLs.

### Improved

- **Conflict safety.** When a note changed both locally and remotely, the
  conflict is detected and surfaced in the plan/log instead of being
  resolved silently. If the chosen strategy would otherwise drop the local
  edits, a `(conflit …)` copy is kept so both versions survive on disk.

- **Idempotent sync state.** A failed sync still persists the sync-state
  registry atomically, so a crash mid-session can never leave a corrupt or
  half-written `.nexanote_sync_state.json`.

### Android & releases

- **Obtainium-friendly APK publishing.** Tagged `vX.Y.Z` releases attach a
  stable-named `NexaNote-Android.apk` asset, so
  [Obtainium](https://github.com/ImranR98/Obtainium) can be pointed at the
  GitHub repo and auto-update from the release tags — no in-app updater, no
  Google Play, no analytics. See the README for setup.
- **Release/version guardrails.** The Android release workflow fails fast if the
  release tag does not match the pubspec `versionName`, or if the build did not
  produce an APK. `pubspec.yaml` stays the single source of truth for the
  version, and Android's versionName/versionCode follow it.
- **Real version in About.** Settings → About now shows the installed version
  read from the platform package metadata (via `package_info_plus`) instead of a
  hardcoded string, and links to the correct GitHub repository.
- **F-Droid alignment kept.** Release metadata stays aligned for a future
  F-Droid submission (applicationId `com.nexanote.app`, `MPL-2.0`, no
  proprietary or Google Play dependencies; the `INTERNET` permission only
  reaches your own backend / WebDAV server).

---

## v1.0.0 — File-based storage

### New

- **File-based storage backend.** Notes are now stored as plain Markdown
  files with YAML frontmatter, drawings as separate JSON files, and
  notebooks as YAML metadata files. The on-disk layout is:

  ```
  <data_dir>/
  ├── notebooks/<notebook_id>.yaml      # Notebook metadata
  ├── notes/<note_id>.md                # Markdown body + YAML frontmatter
  └── drawings/<note_id>.json           # Stylus strokes (one file per note)
  ```

  Single-page typed notes have a clean Markdown body with no NexaNote-specific
  markers, so they can be opened and edited directly in Obsidian, VS Code, or
  any text editor. Multi-page notes use minimal `<!-- nexanote:page N -->`
  markers (HTML comments — invisible in any Markdown renderer) to split page
  contents.

- **Automatic SQLite → file migration.** A pre-v1 `nexanote.db` is detected
  on first startup, every notebook / note / page / stroke is copied into the
  new file layout, and the original database is renamed to
  `nexanote.db.legacy_backup` (never deleted). A `.nexanote_migrated` marker
  prevents the migration from re-running. Soft-deleted notes are preserved
  with their `is_deleted` flag intact.

- **Concurrency-safe writes.** Every note write goes through a
  per-path threading lock and an atomic `tmp + os.replace` swap, so a
  killed process can never leave a half-written note on disk.

- New `nexanote.storage.file_store.FileNoteStore` is the primary storage
  service; `nexanote.storage.legacy_db.NexaNoteDB` is kept solely so the
  migration tool can read pre-v1 databases.

- New `nexanote.storage.migration` module exposes `run_migration(data_dir)`
  and `needs_migration(data_dir)` for scripted migrations.

- New `/health` response includes `"storage": "file"`.

- New `GET /storage` returns `data_dir`, `notes_dir`, `drawings_dir`,
  `notebooks_dir`, and `total_size_mb` (replaces the old SQLite-specific
  `db_path` / `db_size_mb` fields).

### Changed

- Bumped backend version to **1.0.0** (was 0.1.0). API contract is unchanged
  — the Flutter app continues to work without modification.
- Added `PyYAML==6.0.3` to `requirements.txt` for YAML frontmatter parsing.
- Updated `docs/docker.md` to reflect the file-based volume layout.

### Migration notes

- **Existing users**: nothing to do — start the v1.0.0 backend pointing at
  your existing data directory and migration runs automatically on the
  first request. Your `nexanote.db` is renamed to
  `nexanote.db.legacy_backup` and left in place; you can delete it once
  you've verified your notes look right in `notes/` and `drawings/`.
- **WebDAV clients**: the WebDAV layout (notebook/note slug → `note.json`
  + `page_N.ink`) is unchanged. Existing sync clients keep working.
- **Flutter app**: the REST API surface is unchanged, so no app update is
  required to keep using the v1.0.0 backend.

### Non-breaking

This is technically a major version bump because it replaces the storage
engine, but the public REST API and WebDAV layout are stable across the
upgrade. Mobile clients on the v0.x branch keep working against the v1.0.0
backend.

---

## v0.1.x — SQLite storage

Initial public release. SQLite-backed storage, REST API, WebDAV server,
Linux desktop app, Android APK, Docker image, conflict-resolving sync
engine.
