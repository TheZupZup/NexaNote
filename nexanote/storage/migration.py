"""
NexaNote — SQLite → file-based migration / Migration SQLite vers fichier.

EN: Detects an existing pre-v1.0 SQLite database (`nexanote.db`) inside the
    data directory and copies every notebook/note/page/stroke into the new
    file-based layout (notes/*.md, drawings/*.json, notebooks/*.yaml).

    Idempotent: a `.nexanote_migrated` marker is written when the migration
    succeeds. The original SQLite file is renamed to
    `nexanote.db.legacy_backup` so users can recover it if needed; nothing
    is deleted.

FR: Détecte une base SQLite pré-v1.0 (`nexanote.db`) dans le data dir et
    copie chaque carnet/note/page/stroke dans la nouvelle arborescence
    fichier (notes/*.md, drawings/*.json, notebooks/*.yaml).

    Idempotent : un marqueur `.nexanote_migrated` est écrit en cas de
    succès. La base SQLite est renommée en `nexanote.db.legacy_backup`,
    rien n'est supprimé.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nexanote.storage.file_store import FileNoteStore

logger = logging.getLogger("nexanote.storage.migration")

LEGACY_DB_NAME = "nexanote.db"
LEGACY_BACKUP_SUFFIX = ".legacy_backup"
MIGRATION_MARKER = ".nexanote_migrated"


@dataclass
class MigrationReport:
    """Summary of a migration run."""
    ran: bool = False
    notebooks: int = 0
    notes: int = 0
    pages: int = 0
    strokes: int = 0
    errors: list[str] = None  # type: ignore[assignment]
    backup_path: Optional[Path] = None
    skipped_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        if not self.ran:
            return f"Migration skipped: {self.skipped_reason or 'no legacy DB found'}"
        return (
            f"Migration done — {self.notebooks} notebooks, "
            f"{self.notes} notes, {self.pages} pages, {self.strokes} strokes"
            + (f" ({len(self.errors)} errors)" if self.errors else "")
        )


def needs_migration(data_dir: Path) -> bool:
    """True iff a legacy DB is present and migration hasn't already run."""
    data_dir = Path(data_dir)
    legacy_db = data_dir / LEGACY_DB_NAME
    marker = data_dir / MIGRATION_MARKER
    return legacy_db.exists() and not marker.exists()


def run_migration(
    data_dir: Path,
    store: Optional[FileNoteStore] = None,
) -> MigrationReport:
    """
    EN: Migrate the pre-v1.0 SQLite DB at `<data_dir>/nexanote.db` into the
        file-based layout. Safe to call unconditionally — returns
        `ran=False` if there's nothing to do.

    FR: Migre l'ancienne base SQLite vers le stockage fichier. Sûr à
        appeler systématiquement — renvoie `ran=False` s'il n'y a rien
        à faire.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    legacy_db = data_dir / LEGACY_DB_NAME
    marker = data_dir / MIGRATION_MARKER

    report = MigrationReport()

    if marker.exists():
        report.skipped_reason = "already migrated (marker present)"
        return report

    if not legacy_db.exists():
        # Fresh install — write the marker so we don't keep checking.
        marker.write_text(_marker_payload(reason="no legacy DB"), encoding="utf-8")
        report.skipped_reason = "no legacy DB to migrate"
        return report

    # Lazy import — keeps the legacy SQLite layer out of the import graph
    # for fresh installs that never had a DB.
    from nexanote.storage.legacy_db import NexaNoteDB

    logger.info(f"Detected legacy SQLite DB at {legacy_db} — starting migration")
    if store is None:
        store = FileNoteStore(data_dir)

    legacy = NexaNoteDB(legacy_db)
    try:
        # Notebooks first (so notes can reference their parents).
        for nb in legacy.list_notebooks(include_archived=True):
            try:
                store.save_notebook(nb)
                report.notebooks += 1
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"failed to migrate notebook {nb.id[:8]}: {exc}"
                logger.error(msg)
                report.errors.append(msg)

        # Notes (with pages & strokes loaded).
        for note_meta in legacy.list_notes(include_deleted=True, include_archived=True):
            try:
                full = legacy.get_note(note_meta.id, load_pages=True)
                if full is None:
                    continue
                store.save_note(full)
                report.notes += 1
                report.pages += len(full.pages)
                for page in full.pages:
                    report.strokes += len(page.strokes)
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"failed to migrate note {note_meta.id[:8]}: {exc}"
                logger.error(msg)
                report.errors.append(msg)
    finally:
        legacy.close()

    # Move the SQLite file aside so it's preserved but no longer the
    # source of truth.
    backup = legacy_db.with_name(legacy_db.name + LEGACY_BACKUP_SUFFIX)
    try:
        legacy_db.replace(backup)
        report.backup_path = backup
    except OSError as exc:
        msg = f"could not rename legacy DB to {backup.name}: {exc}"
        logger.warning(msg)
        report.errors.append(msg)

    report.ran = True
    marker.write_text(
        _marker_payload(
            reason="migrated from SQLite",
            notebooks=report.notebooks,
            notes=report.notes,
            pages=report.pages,
            strokes=report.strokes,
        ),
        encoding="utf-8",
    )
    logger.info(report.summary())
    return report


def _marker_payload(**fields) -> str:
    """A small JSON payload so the marker doubles as a debug hint."""
    return json.dumps(fields, indent=2)


__all__ = [
    "MigrationReport",
    "needs_migration",
    "run_migration",
    "LEGACY_DB_NAME",
    "MIGRATION_MARKER",
]
