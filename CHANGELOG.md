# Changelog

All notable changes to NexaNote are documented in this file.

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
