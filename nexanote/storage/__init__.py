"""
NexaNote — Storage package.

EN: Public entry points:
      - FileNoteStore         primary storage as of v1.0.0 (file-based)
      - run_migration         SQLite → file migration helper
      - NexaNoteDB            legacy SQLite store, kept for migration
"""

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
    needs_migration,
    run_migration,
)

__all__ = [
    "AutoExportConfig",
    "AutoExporter",
    "FileNoteStore",
    "NexaNoteDB",
    "MigrationReport",
    "needs_migration",
    "run_migration",
    "export_all",
    "export_note",
    "sanitize_filename",
]
