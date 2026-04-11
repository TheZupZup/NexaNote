"""
NexaNote — WebDAV Provider
Expose les carnets et notes comme une arborescence de fichiers WebDAV.

Structure exposée :
  /                          ← racine DAV
  /{notebook_name}/          ← un carnet
  /{notebook_name}/{note}/   ← une note (dossier)
  /{notebook_name}/{note}/note.json      ← métadonnées + contenu texte
  /{notebook_name}/{note}/page_1.ink     ← strokes manuscrits (JSON binaire)
  /{notebook_name}/{note}/page_1.png     ← aperçu (futur)

Cela permet à n'importe quel client WebDAV (Nextcloud, navigateur,
rclone, Cyberduck…) de parcourir et synchroniser les notes.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN, HTTP_NOT_FOUND

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point
from nexanote.storage.database import NexaNoteDB

logger = logging.getLogger("nexanote.webdav")


def _slugify(name: str) -> str:
    """Transforme un nom en slug URL-safe."""
    import re
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "-", name)
    return name or "sans-titre"


def _epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Ressources DAV
# ---------------------------------------------------------------------------

class RootCollection(DAVCollection):
    """
    Racine DAV → liste tous les carnets.
    URL : /
    """

    def __init__(self, path: str, environ: dict, db: NexaNoteDB) -> None:
        super().__init__(path, environ)
        self.db = db

    def get_member_names(self) -> list[str]:
        notebooks = self.db.list_notebooks()
        # On expose chaque carnet comme un dossier slug
        return [_slugify(nb.name) + "__" + nb.id[:8] for nb in notebooks]

    def get_member(self, name: str) -> Optional[DAVCollection]:
        notebooks = self.db.list_notebooks()
        for nb in notebooks:
            slug = _slugify(nb.name) + "__" + nb.id[:8]
            if slug == name:
                return NotebookCollection(
                    self.path.rstrip("/") + "/" + name,
                    self.environ,
                    self.db,
                    nb,
                )
        return None


class NotebookCollection(DAVCollection):
    """
    Un carnet DAV → liste toutes ses notes.
    URL : /{notebook_slug}/
    """

    def __init__(
        self,
        path: str,
        environ: dict,
        db: NexaNoteDB,
        notebook: Notebook,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.notebook = notebook

    def get_display_name(self) -> str:
        return self.notebook.name

    def get_creation_date(self) -> float:
        return _epoch(self.notebook.created_at)

    def get_last_modified(self) -> float:
        return _epoch(self.notebook.updated_at)

    def get_member_names(self) -> list[str]:
        notes = self.db.list_notes(notebook_id=self.notebook.id)
        return [_slugify(n.title) + "__" + n.id[:8] for n in notes]

    def get_member(self, name: str) -> Optional[DAVCollection]:
        notes = self.db.list_notes(notebook_id=self.notebook.id)
        for note in notes:
            slug = _slugify(note.title) + "__" + note.id[:8]
            if slug == name:
                full_note = self.db.get_note(note.id, load_pages=True)
                return NoteCollection(
                    self.path.rstrip("/") + "/" + name,
                    self.environ,
                    self.db,
                    full_note,
                )
        return None

    def create_collection(self, name: str) -> "NotebookCollection":
        """Créer une nouvelle note via MKCOL."""
        from nexanote.models.note import Note
        note = Note(
            notebook_id=self.notebook.id,
            title=name.replace("-", " ").title(),
        )
        note.add_page()
        self.db.save_note(note)
        logger.info(f"Note créée via WebDAV : {note.title}")
        slug = _slugify(note.title) + "__" + note.id[:8]
        full_note = self.db.get_note(note.id, load_pages=True)
        return NoteCollection(
            self.path.rstrip("/") + "/" + slug,
            self.environ,
            self.db,
            full_note,
        )


class NoteCollection(DAVCollection):
    """
    Une note DAV → expose ses fichiers (note.json, page_N.ink).
    URL : /{notebook_slug}/{note_slug}/
    """

    def __init__(
        self,
        path: str,
        environ: dict,
        db: NexaNoteDB,
        note: Note,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.note = note

    def get_display_name(self) -> str:
        return self.note.title

    def get_creation_date(self) -> float:
        return _epoch(self.note.created_at)

    def get_last_modified(self) -> float:
        return _epoch(self.note.updated_at)

    def get_member_names(self) -> list[str]:
        names = ["note.json"]
        for page in self.note.pages:
            names.append(f"page_{page.page_number}.ink")
        return names

    def get_member(self, name: str) -> Optional[DAVNonCollection]:
        if name == "note.json":
            return NoteMetaFile(
                self.path.rstrip("/") + "/" + name,
                self.environ,
                self.db,
                self.note,
            )
        # page_N.ink
        if name.startswith("page_") and name.endswith(".ink"):
            try:
                page_num = int(name[5:-4])
            except ValueError:
                return None
            page = self.note.get_page(page_num)
            if page:
                return InkFile(
                    self.path.rstrip("/") + "/" + name,
                    self.environ,
                    self.db,
                    page,
                    self.note,
                )
        return None


class NoteMetaFile(DAVNonCollection):
    """
    note.json — métadonnées + contenu texte d'une note.
    Readable et writable via GET/PUT.
    """

    def support_etag(self) -> bool:
        return True

    def get_etag(self) -> Optional[str]:
        return f'"{self.note.updated_at.isoformat()}"'

    def __init__(
        self,
        path: str,
        environ: dict,
        db: NexaNoteDB,
        note: Note,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.note = note

    def _serialize(self) -> bytes:
        data = {
            "id": self.note.id,
            "title": self.note.title,
            "type": self.note.note_type.value,
            "tags": self.note.tags,
            "is_pinned": self.note.is_pinned,
            "created_at": self.note.created_at.isoformat(),
            "updated_at": self.note.updated_at.isoformat(),
            "pages": [
                {
                    "page_number": p.page_number,
                    "template": p.template,
                    "typed_content": p.typed_content,
                }
                for p in self.note.pages
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    def get_content_length(self) -> int:
        return len(self._serialize())

    def get_content_type(self) -> str:
        return "application/json; charset=utf-8"

    def get_last_modified(self) -> float:
        return _epoch(self.note.updated_at)

    def get_content(self) -> io.BytesIO:
        return io.BytesIO(self._serialize())

    def begin_write(self, content_type: Optional[str] = None):
        """Reçoit un PUT avec le nouveau contenu note.json."""
        return _NoteMetaWriter(self.db, self.note)


class _NoteMetaWriter(io.RawIOBase):
    """Buffer d'écriture pour note.json — applique les changements à la DB."""

    def __init__(self, db: NexaNoteDB, note: Note) -> None:
        self.db = db
        self.note = note
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        if not self.closed:
            self._buf.seek(0)
            try:
                payload = json.loads(self._buf.read().decode("utf-8"))
                self.note.title = payload.get("title", self.note.title)
                self.note.tags = payload.get("tags", self.note.tags)
                self.note.is_pinned = payload.get("is_pinned", self.note.is_pinned)
                # Mettre à jour le contenu texte des pages
                for page_data in payload.get("pages", []):
                    page = self.note.get_page(page_data["page_number"])
                    if page:
                        page.typed_content = page_data.get(
                            "typed_content", page.typed_content
                        )
                self.note.touch()
                self.db.save_note(self.note)
                logger.info(f"Note mise à jour via WebDAV PUT : {self.note.title}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Erreur parsing note.json : {e}")
        super().close()


class InkFile(DAVNonCollection):
    """
    page_N.ink — données de strokes manuscrits d'une page.
    Format : JSON avec liste de strokes + points.
    Readable et writable.
    """

    def support_etag(self) -> bool:
        return True

    def get_etag(self) -> Optional[str]:
        return f'"{self.page.updated_at.isoformat()}"'

    def __init__(
        self,
        path: str,
        environ: dict,
        db: NexaNoteDB,
        page: Page,
        note: Note,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.page = page
        self.note = note

    def _serialize(self) -> bytes:
        data = {
            "page_id": self.page.id,
            "note_id": self.page.note_id,
            "page_number": self.page.page_number,
            "template": self.page.template,
            "width_px": self.page.width_px,
            "height_px": self.page.height_px,
            "updated_at": self.page.updated_at.isoformat(),
            "strokes": [
                {
                    "id": s.id,
                    "color": s.color,
                    "width": s.width,
                    "tool": s.tool,
                    "created_at": s.created_at.isoformat(),
                    "points": [
                        {
                            "x": p.x,
                            "y": p.y,
                            "pressure": p.pressure,
                            "ts": p.timestamp_ms,
                        }
                        for p in s.points
                    ],
                }
                for s in self.page.strokes
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    def get_content_length(self) -> int:
        return len(self._serialize())

    def get_content_type(self) -> str:
        return "application/json; charset=utf-8"

    def get_last_modified(self) -> float:
        return _epoch(self.page.updated_at)

    def get_content(self) -> io.BytesIO:
        return io.BytesIO(self._serialize())

    def begin_write(self, content_type: Optional[str] = None):
        return _InkWriter(self.db, self.page, self.note)


class _InkWriter(io.RawIOBase):
    """Buffer d'écriture pour page_N.ink — reconstruit les strokes depuis le JSON reçu."""

    def __init__(self, db: NexaNoteDB, page: Page, note: Note) -> None:
        self.db = db
        self.page = page
        self.note = note
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        if not self.closed:
            self._buf.seek(0)
            try:
                payload = json.loads(self._buf.read().decode("utf-8"))
                new_strokes = []
                for s_data in payload.get("strokes", []):
                    points = [
                        Point(
                            x=p["x"],
                            y=p["y"],
                            pressure=p.get("pressure", 0.5),
                            timestamp_ms=p.get("ts", 0),
                        )
                        for p in s_data.get("points", [])
                    ]
                    stroke = InkStroke(
                        id=s_data["id"],
                        color=s_data.get("color", "#000000"),
                        width=s_data.get("width", 2.0),
                        tool=s_data.get("tool", "pen"),
                        points=points,
                    )
                    new_strokes.append(stroke)

                self.page.strokes = new_strokes
                self.page.touch()
                self.db.save_page(self.page)
                self.note.touch()
                self.db.save_note(self.note, save_pages=False)
                logger.info(
                    f"Page {self.page.page_number} mise à jour : "
                    f"{len(new_strokes)} strokes"
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Erreur parsing .ink : {e}")
        super().close()


# ---------------------------------------------------------------------------
# Provider principal
# ---------------------------------------------------------------------------

class NexaNoteDAVProvider(DAVProvider):
    """
    Point d'entrée du provider WebDAV pour NexaNote.
    Enregistré dans le serveur WsgiDAV.
    """

    def __init__(self, db: NexaNoteDB) -> None:
        super().__init__()
        self.db = db
        self.readonly = False

    def get_resource_inst(
        self, path: str, environ: dict
    ) -> Optional[DAVCollection | DAVNonCollection]:
        """
        Résout un chemin DAV vers la ressource correspondante.
        Ex: /mon-carnet__a1b2c3d4/ma-note__e5f6g7h8/note.json
        """
        path = path.rstrip("/") or "/"
        parts = [p for p in path.split("/") if p]

        # Racine
        if not parts:
            return RootCollection("/", environ, self.db)

        # Niveau carnet
        notebooks = self.db.list_notebooks()
        target_nb = None
        for nb in notebooks:
            if (_slugify(nb.name) + "__" + nb.id[:8]) == parts[0]:
                target_nb = nb
                break

        if target_nb is None:
            return None

        nb_path = "/" + parts[0]

        if len(parts) == 1:
            return NotebookCollection(nb_path, environ, self.db, target_nb)

        # Niveau note
        notes = self.db.list_notes(notebook_id=target_nb.id)
        target_note = None
        for note in notes:
            if (_slugify(note.title) + "__" + note.id[:8]) == parts[1]:
                target_note = note
                break

        if target_note is None:
            return None

        full_note = self.db.get_note(target_note.id, load_pages=True)
        note_path = nb_path + "/" + parts[1]

        if len(parts) == 2:
            return NoteCollection(note_path, environ, self.db, full_note)

        # Niveau fichier
        file_name = parts[2]
        note_col = NoteCollection(note_path, environ, self.db, full_note)
        return note_col.get_member(file_name)
