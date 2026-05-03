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
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
from wsgidav import dav_error as _dav_error
from wsgidav.dav_error import DAVError

# EN: Some WsgiDAV builds expose HTTP_* both at module level and via the
#     `dav_error` namespace. Pull them via getattr to stay forward-compatible.
# FR: Certaines versions exposent les codes HTTP aux deux endroits. On y
#     accède via getattr pour rester compatible.
HTTP_FORBIDDEN = getattr(_dav_error, "HTTP_FORBIDDEN", 403)
HTTP_NOT_FOUND = getattr(_dav_error, "HTTP_NOT_FOUND", 404)
HTTP_CONFLICT = getattr(_dav_error, "HTTP_CONFLICT", 409)
HTTP_INTERNAL_ERROR = getattr(_dav_error, "HTTP_INTERNAL_ERROR", 500)
HTTP_BAD_REQUEST = getattr(_dav_error, "HTTP_BAD_REQUEST", 400)

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point
from nexanote.storage.file_store import FileNoteStore

logger = logging.getLogger("nexanote.webdav")


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

# EN: Slugs follow `<title-slug>__<id-prefix>`. The id-prefix is the first
#     8 hex chars of a UUID — used to disambiguate notes/notebooks that
#     share a title.
# FR: Les slugs suivent `<title-slug>__<id-prefix>`. Le préfixe ID = 8 hex
#     du UUID, pour différencier des notes au même titre.

_SLUG_SEPARATOR = "__"
_HEX_RE = re.compile(r"^[0-9a-f]{1,16}$")

# EN: The fallback "Uncategorized" notebook is exposed at a bare-name slug so
#     clients can target it without knowing its synthetic id. The literal id
#     prefix (all zeros) is also accepted for legacy callers.
# FR: Le carnet "Uncategorized" est exposé sous un slug court ("uncategorized")
#     pour que les clients n'aient pas à connaître l'ID interne.
_DEFAULT_NB_ID_PREFIX = "00000000"
_DEFAULT_NB_SLUG = "uncategorized"


def _slugify(name: str) -> str:
    """Transforme un nom en slug URL-safe."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "-", name)
    return name or "sans-titre"


def _parse_slug(slug: str) -> tuple[str, Optional[str]]:
    """
    EN: Parse a slug like ``my-note__abcd1234`` into a human-readable title
        and an optional 8-char hex id-prefix. If no separator is present,
        the prefix is None.
    FR: Décompose un slug en (titre lisible, préfixe d'ID).
    """
    if _SLUG_SEPARATOR in slug:
        title_slug, _, id_prefix = slug.rpartition(_SLUG_SEPARATOR)
        if id_prefix and _HEX_RE.match(id_prefix):
            title = title_slug.replace("-", " ").strip().title() or "Sans titre"
            return title, id_prefix
    title = slug.replace("-", " ").strip().title() or "Sans titre"
    return title, None


def _notebook_slug(nb: Notebook) -> str:
    """
    EN: Canonical slug for a notebook. The synthetic "Uncategorized" notebook
        gets a bare slug so it lines up with the well-known fallback name
        used by sync clients.
    FR: Slug canonique d'un carnet. "Uncategorized" est exposé sous le slug
        court connu des clients de synchro.
    """
    if nb.id.startswith(_DEFAULT_NB_ID_PREFIX):
        return _DEFAULT_NB_SLUG
    return _slugify(nb.name) + _SLUG_SEPARATOR + nb.id[:8]


def _id_with_prefix(prefix: Optional[str]) -> str:
    """
    EN: Mint a UUID-formatted id whose first 8 hex chars match ``prefix``
        (when given). Lets the WebDAV slug round-trip cleanly through
        MKCOL → PUT → PROPFIND without mismatches.
    FR: Génère un UUID dont les 8 premiers caractères correspondent à
        ``prefix`` — pour que le slug WebDAV reste stable.
    """
    fresh = uuid.uuid4().hex
    if prefix and _HEX_RE.match(prefix) and len(prefix) >= 1:
        prefix_hex = prefix.lower()[:8].ljust(8, "0")
        fresh = prefix_hex + fresh[8:]
    return f"{fresh[0:8]}-{fresh[8:12]}-{fresh[12:16]}-{fresh[16:20]}-{fresh[20:32]}"


def _epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _safe_etag(dt: datetime) -> str:
    """
    EN: Return an ETag token for ``dt`` that satisfies WsgiDAV's rules:
        no surrounding quotes, no embedded quotes, not a weak tag. WsgiDAV
        wraps it in quotes when emitting the header.
    FR: Token ETag conforme à WsgiDAV (sans guillemets, ni W/).
    """
    return dt.replace(tzinfo=timezone.utc).isoformat().replace('"', "")


def _safe_dav_error(exc: BaseException, default_msg: str) -> DAVError:
    """
    EN: Wrap any non-DAVError exception into a 500 with a short, useful
        message — so writers don't crash WsgiDAV with a bare traceback.
    FR: Convertit une exception en DAVError 500 avec un message lisible.
    """
    if isinstance(exc, DAVError):
        return exc
    return DAVError(
        HTTP_INTERNAL_ERROR,
        context_info=f"{default_msg}: {type(exc).__name__}: {exc}",
        src_exception=exc,
    )


# ---------------------------------------------------------------------------
# Ressources DAV
# ---------------------------------------------------------------------------

class RootCollection(DAVCollection):
    """
    Racine DAV → liste tous les carnets.
    URL : /
    Supporte aussi MKCOL pour créer un nouveau carnet.
    """

    def __init__(self, path: str, environ: dict, db: FileNoteStore) -> None:
        super().__init__(path, environ)
        self.db = db

    def get_member_names(self) -> list[str]:
        notebooks = self.db.list_notebooks()
        # On expose chaque carnet comme un dossier slug
        return [_notebook_slug(nb) for nb in notebooks]

    def get_member(self, name: str) -> Optional[DAVCollection]:
        notebook = _find_notebook_by_slug(self.db, name)
        if notebook is None:
            return None
        return NotebookCollection(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.db,
            notebook,
        )

    def create_collection(self, name: str) -> "NotebookCollection":
        """
        EN: MKCOL on the root creates a fresh notebook. The slug is parsed
            so the on-disk notebook keeps the same id-prefix the client
            advertised — this way the slug is stable across MKCOL → PUT.
        FR: MKCOL à la racine crée un nouveau carnet. Le slug est analysé
            pour préserver le préfixe d'ID — slug stable entre les requêtes.
        """
        if not name or "/" in name:
            raise DAVError(HTTP_BAD_REQUEST, context_info="invalid notebook name")

        existing = _find_notebook_by_slug(self.db, name)
        if existing is not None:
            # Idempotent — return the existing notebook so retries don't fail.
            return NotebookCollection(
                self.path.rstrip("/") + "/" + name,
                self.environ,
                self.db,
                existing,
            )

        title, id_prefix = _parse_slug(name)
        try:
            notebook = Notebook(
                id=_id_with_prefix(id_prefix),
                name=title,
            )
            self.db.save_notebook(notebook)
        except Exception as exc:
            logger.exception("MKCOL notebook failed: %s", name)
            raise _safe_dav_error(exc, "could not create notebook") from exc

        logger.info("Carnet créé via WebDAV MKCOL : %s", title)
        return NotebookCollection(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.db,
            notebook,
        )


class NotebookCollection(DAVCollection):
    """
    Un carnet DAV → liste toutes ses notes.
    URL : /{notebook_slug}/
    """

    def __init__(
        self,
        path: str,
        environ: dict,
        db: FileNoteStore,
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
        return [_slugify(n.title) + _SLUG_SEPARATOR + n.id[:8] for n in notes]

    def get_member(self, name: str) -> Optional[DAVCollection]:
        note = _find_note_by_slug(self.db, self.notebook.id, name)
        if note is None:
            return None
        full_note = self.db.get_note(note.id, load_pages=True)
        return NoteCollection(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.db,
            full_note,
        )

    def create_collection(self, name: str) -> "NoteCollection":
        """
        EN: MKCOL on a notebook creates a fresh note. The note's id-prefix
            is taken from the slug so the path stays valid for the follow-
            up PUT note.json/page_N.ink — no slug churn between requests.
        FR: MKCOL sur un carnet crée une note. Le préfixe d'ID est repris
            du slug pour que le PUT suivant trouve la bonne ressource.
        """
        if not name or "/" in name:
            raise DAVError(HTTP_BAD_REQUEST, context_info="invalid note name")

        existing = _find_note_by_slug(self.db, self.notebook.id, name)
        if existing is not None:
            full_note = self.db.get_note(existing.id, load_pages=True)
            return NoteCollection(
                self.path.rstrip("/") + "/" + name,
                self.environ,
                self.db,
                full_note,
            )

        title, id_prefix = _parse_slug(name)
        try:
            note = Note(
                id=_id_with_prefix(id_prefix),
                notebook_id=self.notebook.id,
                title=title,
            )
            note.add_page()
            self.db.save_note(note)
        except Exception as exc:
            logger.exception("MKCOL note failed: %s", name)
            raise _safe_dav_error(exc, "could not create note") from exc

        logger.info("Note créée via WebDAV MKCOL : %s", note.title)
        full_note = self.db.get_note(note.id, load_pages=True)
        return NoteCollection(
            self.path.rstrip("/") + "/" + name,
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
        db: FileNoteStore,
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
        page_num = _parse_page_filename(name)
        if page_num is not None:
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

    def create_empty_resource(self, name: str) -> DAVNonCollection:
        """
        EN: PUT to a not-yet-existing file inside a note (typically a new
            page_N.ink). We allocate the page on the fly so the writer can
            stream content into it. note.json always returns the existing
            placeholder (a note always has metadata).
        FR: PUT sur un fichier inexistant (ex: page_N.ink d'une nouvelle
            page). On crée la page à la volée pour que l'écriture aboutisse.
        """
        if name == "note.json":
            # note.json always exists conceptually — return the writable view.
            return NoteMetaFile(
                self.path.rstrip("/") + "/" + name,
                self.environ,
                self.db,
                self.note,
            )

        page_num = _parse_page_filename(name)
        if page_num is None:
            raise DAVError(
                HTTP_FORBIDDEN,
                context_info=f"unsupported file name: {name}",
            )

        # Create a new (empty) page on the note so the upcoming write target
        # is well-defined. The actual content is filled by `_InkWriter`.
        try:
            page = self.note.get_page(page_num)
            if page is None:
                page = Page(note_id=self.note.id, page_number=page_num)
                self.note.pages.append(page)
                self.note.pages.sort(key=lambda p: p.page_number)
                self.note.touch()
                self.db.save_note(self.note)
        except Exception as exc:
            logger.exception("create_empty_resource failed: %s", name)
            raise _safe_dav_error(exc, "could not create page") from exc

        return InkFile(
            self.path.rstrip("/") + "/" + name,
            self.environ,
            self.db,
            page,
            self.note,
        )


def _parse_page_filename(name: str) -> Optional[int]:
    """Return the page number for ``page_<N>.ink`` or None."""
    if not name.startswith("page_") or not name.endswith(".ink"):
        return None
    try:
        return int(name[len("page_"): -len(".ink")])
    except ValueError:
        return None


def _find_notebook_by_slug(db: FileNoteStore, slug: str) -> Optional[Notebook]:
    """
    EN: Resolve a path component to a notebook. Tries the canonical slug,
        then id-prefix (handles renames), then slugified name (handles
        slugs without an id, like the well-known ``uncategorized``).
    FR: Résout un composant de chemin vers un carnet.
    """
    notebooks = db.list_notebooks()
    for nb in notebooks:
        if _notebook_slug(nb) == slug:
            return nb
    _, id_prefix = _parse_slug(slug)
    if id_prefix:
        for nb in notebooks:
            if nb.id.startswith(id_prefix):
                return nb
    if _SLUG_SEPARATOR not in slug:
        for nb in notebooks:
            if _slugify(nb.name) == slug:
                return nb
    return None


def _find_note_by_slug(db: FileNoteStore, notebook_id: str, slug: str) -> Optional[Note]:
    """Same lookup strategy as ``_find_notebook_by_slug`` for notes."""
    notes = db.list_notes(notebook_id=notebook_id)
    for n in notes:
        if (_slugify(n.title) + _SLUG_SEPARATOR + n.id[:8]) == slug:
            return n
    _, id_prefix = _parse_slug(slug)
    if id_prefix:
        for n in notes:
            if n.id.startswith(id_prefix):
                return n
    if _SLUG_SEPARATOR not in slug:
        for n in notes:
            if _slugify(n.title) == slug:
                return n
    return None


# ---------------------------------------------------------------------------
# File-like resources (note.json, page_N.ink)
# ---------------------------------------------------------------------------

class NoteMetaFile(DAVNonCollection):
    """
    note.json — métadonnées + contenu texte d'une note.
    Readable et writable via GET/PUT.
    """

    def support_etag(self) -> bool:
        return True

    def get_etag(self) -> Optional[str]:
        # WsgiDAV's `checked_etag` rejects values containing quotes — return
        # the raw token; the framework adds the surrounding quotes for the
        # ETag header itself. Returning a pre-quoted value used to crash
        # WsgiDAV with a 500 on PUT to existing notes (e.g. the welcome note).
        return _safe_etag(self.note.updated_at)

    def __init__(
        self,
        path: str,
        environ: dict,
        db: FileNoteStore,
        note: Note,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.note = note

    def _serialize(self) -> bytes:
        try:
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
        except Exception as exc:
            # Surface the underlying reason instead of a bare 500. WsgiDAV
            # calls `_serialize` from `get_content_length`/`get_content`,
            # which run before `begin_write` on PUT — a crash here would
            # otherwise become "WebDAV upload failed: 500 Internal Server
            # Error" with no clue about the offending field.
            logger.exception("note.json serialization failed for %s", self.note.id)
            raise _safe_dav_error(exc, "could not serialize note.json") from exc

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

    def __init__(self, db: FileNoteStore, note: Note) -> None:
        self.db = db
        self.note = note
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        if self.closed:
            super().close()
            return

        try:
            self._buf.seek(0)
            raw = self._buf.read()
        finally:
            super().close()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("note.json: invalid JSON body: %s", exc)
            raise DAVError(
                HTTP_BAD_REQUEST,
                context_info=f"invalid note.json body: {exc}",
            ) from exc

        try:
            # If MKCOL minted a placeholder id that the PUT body now wants to
            # replace (e.g. client and server both generated UUIDs sharing
            # only the slug's 8-char prefix), drop the placeholder so the
            # client's full id wins. Without this the on-disk file id would
            # diverge from the client's id and break future updates.
            payload_id = payload.get("id")
            if (
                isinstance(payload_id, str)
                and payload_id
                and payload_id != self.note.id
            ):
                old_id = self.note.id
                self.note.id = payload_id
                for page in self.note.pages:
                    page.note_id = payload_id
                try:
                    self.db.delete_note_permanent(old_id)
                except Exception:
                    logger.warning(
                        "could not delete placeholder note %s — ignoring", old_id
                    )

            self.note.title = payload.get("title", self.note.title)
            self.note.tags = payload.get("tags", self.note.tags) or []
            self.note.is_pinned = bool(
                payload.get("is_pinned", self.note.is_pinned)
            )
            note_type = payload.get("type")
            if note_type:
                try:
                    self.note.note_type = NoteType(note_type)
                except ValueError:
                    logger.warning("ignoring unknown note type: %s", note_type)
            for page_data in payload.get("pages") or []:
                page_number = page_data.get("page_number")
                if page_number is None:
                    continue
                page = self.note.get_page(page_number)
                if page is None:
                    page = Page(
                        note_id=self.note.id,
                        page_number=int(page_number),
                        template=page_data.get("template", "blank"),
                    )
                    self.note.pages.append(page)
                page.typed_content = page_data.get(
                    "typed_content", page.typed_content
                )
            self.note.pages.sort(key=lambda p: p.page_number)
            self.note.touch()
            self.db.save_note(self.note)
            logger.info("Note mise à jour via WebDAV PUT : %s", self.note.title)
        except DAVError:
            raise
        except Exception as exc:
            logger.exception("note.json write failed")
            raise _safe_dav_error(exc, "saving note failed") from exc


class InkFile(DAVNonCollection):
    """
    page_N.ink — données de strokes manuscrits d'une page.
    Format : JSON avec liste de strokes + points.
    Readable et writable.
    """

    def support_etag(self) -> bool:
        return True

    def get_etag(self) -> Optional[str]:
        return _safe_etag(self.page.updated_at)

    def __init__(
        self,
        path: str,
        environ: dict,
        db: FileNoteStore,
        page: Page,
        note: Note,
    ) -> None:
        super().__init__(path, environ)
        self.db = db
        self.page = page
        self.note = note

    def _serialize(self) -> bytes:
        try:
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
        except Exception as exc:
            logger.exception(
                "page_%d.ink serialization failed for note %s",
                self.page.page_number,
                self.note.id,
            )
            raise _safe_dav_error(
                exc, f"could not serialize page_{self.page.page_number}.ink"
            ) from exc

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

    def __init__(self, db: FileNoteStore, page: Page, note: Note) -> None:
        self.db = db
        self.page = page
        self.note = note
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def close(self) -> None:
        if self.closed:
            super().close()
            return

        try:
            self._buf.seek(0)
            raw = self._buf.read()
        finally:
            super().close()

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("page_N.ink: invalid JSON body: %s", exc)
            raise DAVError(
                HTTP_BAD_REQUEST,
                context_info=f"invalid ink page body: {exc}",
            ) from exc

        try:
            new_strokes: list[InkStroke] = []
            for s_data in payload.get("strokes") or []:
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
                "Page %d mise à jour : %d strokes",
                self.page.page_number,
                len(new_strokes),
            )
        except DAVError:
            raise
        except Exception as exc:
            logger.exception("page ink write failed")
            raise _safe_dav_error(exc, "saving page failed") from exc


# ---------------------------------------------------------------------------
# Provider principal
# ---------------------------------------------------------------------------

class NexaNoteDAVProvider(DAVProvider):
    """
    Point d'entrée du provider WebDAV pour NexaNote.
    Enregistré dans le serveur WsgiDAV.
    """

    def __init__(self, db: FileNoteStore) -> None:
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
        target_nb = _find_notebook_by_slug(self.db, parts[0])
        if target_nb is None:
            return None

        nb_path = "/" + parts[0]

        if len(parts) == 1:
            return NotebookCollection(nb_path, environ, self.db, target_nb)

        # Niveau note
        target_note = _find_note_by_slug(self.db, target_nb.id, parts[1])
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
