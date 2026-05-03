"""
NexaNote — Storage package.

EN: Public entry points:
      - FileNoteStore          YAML-frontmatter backend (default).
      - PlainMarkdownNoteStore plain Markdown + JSON-sidecar backend.
      - create_store           factory that picks the backend from the
                               on-disk marker / env var.
      - run_migration          legacy SQLite → file-store migration.
      - migrate_yaml_to_plain  YAML store → plain Markdown migration.
      - NexaNoteDB             legacy SQLite store, kept for migration.
FR: Points d'entrée publics du package de stockage.
"""

from nexanote.storage.backend import (
    DEFAULT_MODE,
    ENV_STORAGE_MODE,
    MODE_MARKER,
    MODE_PLAIN,
    MODE_YAML,
    BackendInfo,
    NoteStore,
    create_store,
    detect_mode,
    write_mode_marker,
)
from nexanote.storage.export import (
    AutoExportConfig,
    AutoExporter,
    export_all,
    export_note,
    sanitize_filename,
)
from nexanote.storage.file_store import FileNoteStore
from nexanote.storage.legacy_db import NexaNoteDB
from nexanote.storage.migration import (
    MigrationReport,
    PlainMigrationReport,
    migrate_yaml_to_plain,
    needs_migration,
    run_migration,
)
from nexanote.storage.plain_store import PlainMarkdownNoteStore

__all__ = [
    "AutoExportConfig",
    "AutoExporter",
    "BackendInfo",
    "DEFAULT_MODE",
    "ENV_STORAGE_MODE",
    "FileNoteStore",
    "MODE_MARKER",
    "MODE_PLAIN",
    "MODE_YAML",
    "MigrationReport",
    "NexaNoteDB",
    "NoteStore",
    "PlainMarkdownNoteStore",
    "PlainMigrationReport",
    "create_store",
    "detect_mode",
    "export_all",
    "export_note",
    "migrate_yaml_to_plain",
    "needs_migration",
    "run_migration",
    "sanitize_filename",
    "write_mode_marker",
]
