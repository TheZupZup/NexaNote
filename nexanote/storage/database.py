"""
NexaNote — Couche de stockage SQLite
Gère la persistance locale des notes, carnets et pages.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from nexanote.models.note import (
    InkStroke,
    Note,
    Notebook,
    NoteType,
    Page,
    Point,
    SyncStatus,
)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _fmt_dt(dt: datetime) -> str:
    return dt.isoformat()


class NexaNoteDB:
    """
    Gestionnaire de base de données SQLite pour NexaNote.
    Toutes les opérations CRUD passent par ici.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")   # Meilleure perf concurrente
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Initialisation du schéma
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notebooks (
                id          TEXT PRIMARY KEY,
                parent_id   TEXT,
                name        TEXT NOT NULL DEFAULT 'Nouveau carnet',
                description TEXT NOT NULL DEFAULT '',
                color       TEXT NOT NULL DEFAULT '#6366f1',
                icon        TEXT NOT NULL DEFAULT 'notebook',
                is_archived INTEGER NOT NULL DEFAULT 0,
                sync_status TEXT NOT NULL DEFAULT 'local_only',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES notebooks(id)
            );

            CREATE TABLE IF NOT EXISTS notes (
                id          TEXT PRIMARY KEY,
                notebook_id TEXT,
                title       TEXT NOT NULL DEFAULT 'Sans titre',
                note_type   TEXT NOT NULL DEFAULT 'typed',
                tags        TEXT NOT NULL DEFAULT '[]',
                is_pinned   INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                is_deleted  INTEGER NOT NULL DEFAULT 0,
                sync_status TEXT NOT NULL DEFAULT 'local_only',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id)
            );

            CREATE TABLE IF NOT EXISTS pages (
                id            TEXT PRIMARY KEY,
                note_id       TEXT NOT NULL,
                page_number   INTEGER NOT NULL DEFAULT 1,
                template      TEXT NOT NULL DEFAULT 'blank',
                width_px      REAL NOT NULL DEFAULT 1404.0,
                height_px     REAL NOT NULL DEFAULT 1872.0,
                typed_content TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS strokes (
                id         TEXT PRIMARY KEY,
                page_id    TEXT NOT NULL,
                color      TEXT NOT NULL DEFAULT '#000000',
                width      REAL NOT NULL DEFAULT 2.0,
                tool       TEXT NOT NULL DEFAULT 'pen',
                points     TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_notes_notebook ON notes(notebook_id);
            CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pages_note ON pages(note_id);
            CREATE INDEX IF NOT EXISTS idx_strokes_page ON strokes(page_id);
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Sérialisation / désérialisation
    # ------------------------------------------------------------------

    def _row_to_notebook(self, row: sqlite3.Row) -> Notebook:
        return Notebook(
            id=row["id"],
            parent_id=row["parent_id"],
            name=row["name"],
            description=row["description"],
            color=row["color"],
            icon=row["icon"],
            is_archived=bool(row["is_archived"]),
            sync_status=SyncStatus(row["sync_status"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _row_to_note(self, row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            notebook_id=row["notebook_id"],
            title=row["title"],
            note_type=NoteType(row["note_type"]),
            tags=json.loads(row["tags"]),
            is_pinned=bool(row["is_pinned"]),
            is_archived=bool(row["is_archived"]),
            is_deleted=bool(row["is_deleted"]),
            sync_status=SyncStatus(row["sync_status"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            pages=[],  # Chargé séparément si nécessaire
        )

    def _row_to_page(self, row: sqlite3.Row, strokes: list[InkStroke]) -> Page:
        return Page(
            id=row["id"],
            note_id=row["note_id"],
            page_number=row["page_number"],
            template=row["template"],
            width_px=row["width_px"],
            height_px=row["height_px"],
            typed_content=row["typed_content"],
            strokes=strokes,
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _row_to_stroke(self, row: sqlite3.Row) -> InkStroke:
        raw_points = json.loads(row["points"])
        points = [
            Point(
                x=p["x"],
                y=p["y"],
                pressure=p.get("pressure", 0.5),
                timestamp_ms=p.get("ts", 0),
            )
            for p in raw_points
        ]
        return InkStroke(
            id=row["id"],
            points=points,
            color=row["color"],
            width=row["width"],
            tool=row["tool"],
            created_at=_parse_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # Notebooks CRUD
    # ------------------------------------------------------------------

    def save_notebook(self, nb: Notebook) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO notebooks
                    (id, parent_id, name, description, color, icon,
                     is_archived, sync_status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    parent_id=excluded.parent_id,
                    name=excluded.name,
                    description=excluded.description,
                    color=excluded.color,
                    icon=excluded.icon,
                    is_archived=excluded.is_archived,
                    sync_status=excluded.sync_status,
                    updated_at=excluded.updated_at
                """,
                (
                    nb.id, nb.parent_id, nb.name, nb.description,
                    nb.color, nb.icon, int(nb.is_archived),
                    nb.sync_status.value,
                    _fmt_dt(nb.created_at), _fmt_dt(nb.updated_at),
                ),
            )

    def get_notebook(self, notebook_id: str) -> Optional[Notebook]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM notebooks WHERE id = ?", (notebook_id,)
        ).fetchone()
        return self._row_to_notebook(row) if row else None

    def list_notebooks(self, include_archived: bool = False) -> list[Notebook]:
        conn = self._get_conn()
        query = "SELECT * FROM notebooks"
        if not include_archived:
            query += " WHERE is_archived = 0"
        query += " ORDER BY name"
        rows = conn.execute(query).fetchall()
        return [self._row_to_notebook(r) for r in rows]

    def delete_notebook(self, notebook_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))

    # ------------------------------------------------------------------
    # Notes CRUD
    # ------------------------------------------------------------------

    def save_note(self, note: Note, save_pages: bool = True) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO notes
                    (id, notebook_id, title, note_type, tags,
                     is_pinned, is_archived, is_deleted,
                     sync_status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    notebook_id=excluded.notebook_id,
                    title=excluded.title,
                    note_type=excluded.note_type,
                    tags=excluded.tags,
                    is_pinned=excluded.is_pinned,
                    is_archived=excluded.is_archived,
                    is_deleted=excluded.is_deleted,
                    sync_status=excluded.sync_status,
                    updated_at=excluded.updated_at
                """,
                (
                    note.id, note.notebook_id, note.title,
                    note.note_type.value, json.dumps(note.tags),
                    int(note.is_pinned), int(note.is_archived),
                    int(note.is_deleted), note.sync_status.value,
                    _fmt_dt(note.created_at), _fmt_dt(note.updated_at),
                ),
            )

        if save_pages:
            for page in note.pages:
                self.save_page(page)

    def get_note(self, note_id: str, load_pages: bool = True) -> Optional[Note]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if not row:
            return None
        note = self._row_to_note(row)
        if load_pages:
            note.pages = self.list_pages(note_id)
        return note

    def list_notes(
        self,
        notebook_id: Optional[str] = None,
        include_deleted: bool = False,
        include_archived: bool = False,
        search_title: Optional[str] = None,
    ) -> list[Note]:
        conn = self._get_conn()
        conditions = []
        params: list = []

        if not include_deleted:
            conditions.append("is_deleted = 0")
        if not include_archived:
            conditions.append("is_archived = 0")
        if notebook_id is not None:
            conditions.append("notebook_id = ?")
            params.append(notebook_id)
        if search_title:
            conditions.append("title LIKE ?")
            params.append(f"%{search_title}%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT * FROM notes {where} ORDER BY updated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_note(r) for r in rows]

    def delete_note_permanent(self, note_id: str) -> None:
        """Suppression définitive (purge de la corbeille)."""
        with self._transaction() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    # ------------------------------------------------------------------
    # Pages CRUD
    # ------------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO pages
                    (id, note_id, page_number, template,
                     width_px, height_px, typed_content,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    page_number=excluded.page_number,
                    template=excluded.template,
                    typed_content=excluded.typed_content,
                    updated_at=excluded.updated_at
                """,
                (
                    page.id, page.note_id, page.page_number,
                    page.template, page.width_px, page.height_px,
                    page.typed_content,
                    _fmt_dt(page.created_at), _fmt_dt(page.updated_at),
                ),
            )
        for stroke in page.strokes:
            self.save_stroke(stroke, page.id)

    def list_pages(self, note_id: str) -> list[Page]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM pages WHERE note_id = ? ORDER BY page_number",
            (note_id,),
        ).fetchall()
        pages = []
        for row in rows:
            strokes = self.list_strokes(row["id"])
            pages.append(self._row_to_page(row, strokes))
        return pages

    # ------------------------------------------------------------------
    # Strokes CRUD
    # ------------------------------------------------------------------

    def save_stroke(self, stroke: InkStroke, page_id: str) -> None:
        points_json = json.dumps([
            {"x": p.x, "y": p.y, "pressure": p.pressure, "ts": p.timestamp_ms}
            for p in stroke.points
        ])
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strokes
                    (id, page_id, color, width, tool, points, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    stroke.id, page_id, stroke.color, stroke.width,
                    stroke.tool, points_json, _fmt_dt(stroke.created_at),
                ),
            )

    def list_strokes(self, page_id: str) -> list[InkStroke]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM strokes WHERE page_id = ? ORDER BY created_at",
            (page_id,),
        ).fetchall()
        return [self._row_to_stroke(r) for r in rows]

    def delete_stroke(self, stroke_id: str) -> None:
        with self._transaction() as conn:
            conn.execute("DELETE FROM strokes WHERE id = ?", (stroke_id,))

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        conn = self._get_conn()
        return {
            "notebooks": conn.execute(
                "SELECT COUNT(*) FROM notebooks WHERE is_archived=0"
            ).fetchone()[0],
            "notes": conn.execute(
                "SELECT COUNT(*) FROM notes WHERE is_deleted=0 AND is_archived=0"
            ).fetchone()[0],
            "notes_deleted": conn.execute(
                "SELECT COUNT(*) FROM notes WHERE is_deleted=1"
            ).fetchone()[0],
            "pages": conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
            "strokes": conn.execute("SELECT COUNT(*) FROM strokes").fetchone()[0],
        }
