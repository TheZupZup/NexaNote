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

import hashlib
import io
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
from nexanote.storage.file_store import FileNoteStore, _atomic_write

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


def _safe_etag(*parts: object) -> str:
    """
    EN: Return a strong ETag token derived from the given identity ``parts``
        (id, page number, last-modified timestamp…). WsgiDAV's ``checked_etag``
        rejects values containing ``"`` or starting with ``W/``; we return a
        sha256 hex digest, which is guaranteed quote-free. WsgiDAV wraps the
        token in quotes when emitting the header — never pre-quote it here.
    FR: Token ETag fort à partir d'éléments d'identité (sha256 hexa). Jamais
        de guillemets : WsgiDAV les ajoute lui-même.
    """
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, datetime):
            value = part.replace(tzinfo=timezone.utc).isoformat()
        else:
            value = str(part)
        h.update(value.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


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
            _placeholders(self.db).add(note.id)
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
        # WsgiDAV's `checked_etag` rejects values containing quotes — never
        # pre-quote here; the framework wraps the token in quotes for the
        # header. A raw ISO timestamp also worked, but a sha256 of the note
        # identity changes only when something a client cares about changed.
        return _safe_etag("note", self.note.id, self.note.updated_at)

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
            placeholder_id: Optional[str] = None
            payload_id = payload.get("id")
            if (
                isinstance(payload_id, str)
                and payload_id
                and payload_id != self.note.id
            ):
                # EN: The note behind this path can also be a real note found
                #     by the title-slug or id-prefix fallbacks, and a real
                #     note can be empty and share the 8-char prefix. Only a
                #     note this server recorded as an MKCOL placeholder may be
                #     replaced; anything else gets a 409.
                # FR: Seul un placeholder enregistré lors du MKCOL peut être
                #     remplacé ; une vraie note (même vide) donne un 409.
                if not _is_placeholder_for(self.db, self.note, payload_id):
                    raise DAVError(
                        HTTP_CONFLICT,
                        context_info="note.json id does not match the note at this path",
                    )
                placeholder = self.note
                placeholder_id = placeholder.id
                # The placeholder holds nothing but what this client sent
                # (checked above) and its content is carried over in memory,
                # so its files go first: in the plain store the claimed note
                # then gets the placeholder's file name, not "Doc (2).md".
                try:
                    self.db.delete_note_permanent(placeholder_id)
                except Exception:
                    logger.warning(
                        "could not delete placeholder note %s — ignoring",
                        placeholder_id,
                    )
                existing = self.db.get_note(payload_id, load_pages=True)
                if existing is not None:
                    # The client's note already exists here (e.g. it moved to
                    # this notebook): update it, keeping its pages and
                    # drawings, instead of overwriting it with the
                    # placeholder's empty ones. A page this client already
                    # sent ink for (into the placeholder) takes that ink, as
                    # the same PUT would have done on the real note.
                    existing.notebook_id = placeholder.notebook_id
                    for page in placeholder.pages:
                        if not page.strokes:
                            continue
                        target = existing.get_page(page.page_number)
                        if target is None:
                            page.note_id = payload_id
                            existing.pages.append(page)
                        else:
                            target.strokes = page.strokes
                    existing.pages.sort(key=lambda p: p.page_number)
                    self.note = existing
                else:
                    self.note.id = payload_id
                    for page in self.note.pages:
                        page.note_id = payload_id

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
                _apply_client_timestamp(page, page_data.get("updated_at"))
            self.note.pages.sort(key=lambda p: p.page_number)
            if not _apply_client_timestamp(self.note, payload.get("updated_at")):
                self.note.touch()
            self.db.save_note(self.note)
            if placeholder_id is not None:
                _placeholders(self.db).remove(placeholder_id)
            logger.info("Note mise à jour via WebDAV PUT : %s", self.note.title)
        except DAVError:
            raise
        except Exception as exc:
            logger.exception("note.json write failed")
            raise _safe_dav_error(exc, "saving note failed") from exc


def _is_placeholder_for(db: FileNoteStore, note: Note, payload_id: str) -> bool:
    """
    True only for a note this server created itself as a stand-in (MKCOL, or
    a PUT into a path that didn't exist yet) for the client note
    `payload_id`. Being empty and sharing the id prefix is not enough: a real
    note can be both.

    The placeholder must also hold nothing but what the client itself sent:
    no text (the claim replaces it with the payload's) and no strokes other
    than the ones received through WebDAV for it (a page_N.ink PUT can land
    before note.json). Text or a drawing someone made in the stand-in
    through the app would otherwise be overwritten by the client's push.
    """
    if note.id[:8] != payload_id[:8]:
        return False
    client_strokes = _placeholders(db).client_strokes(note.id)
    if client_strokes is None:
        return False
    return all(
        not p.typed_content and all(s.id in client_strokes for s in p.strokes)
        for p in note.pages
    )


class _PlaceholderRegistry:
    """
    EN: Stand-in notes created ahead of a client's note.json, with the ids of
        the strokes the client has PUT into each one, persisted in the data
        directory so a server restart between MKCOL and PUT doesn't turn the
        placeholder into an unclaimable note.
    FR: Notes provisoires créées avant le note.json du client (et traits reçus
        par WebDAV pour chacune), persistées pour survivre à un redémarrage.
    """

    FILE_NAME = ".webdav_placeholders.json"
    _lock = threading.Lock()

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / self.FILE_NAME

    def _read(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            # Unreadable registry: claim nothing. A placeholder then gets a
            # 409 instead of a real note possibly being replaced.
            logger.warning("unreadable placeholder registry %s: %s", self.path, exc)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            k: [i for i in v if isinstance(i, str)]
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, list)
        }

    def _write(self, entries: dict[str, list[str]]) -> None:
        _atomic_write(self.path, json.dumps(entries, sort_keys=True).encode("utf-8"))

    def add(self, note_id: str) -> None:
        with self._lock:
            entries = self._read()
            entries.setdefault(note_id, [])
            self._write(entries)

    def record_strokes(self, note_id: str, stroke_ids: list[str]) -> None:
        """Remembers strokes the client sent through WebDAV, if [note_id] is
        a placeholder; a no-op for any other note."""
        with self._lock:
            entries = self._read()
            if note_id not in entries:
                return
            entries[note_id] = sorted(set(entries[note_id]) | set(stroke_ids))
            self._write(entries)

    def remove(self, note_id: str) -> None:
        with self._lock:
            entries = self._read()
            if entries.pop(note_id, None) is not None:
                self._write(entries)

    def client_strokes(self, note_id: str) -> Optional[set[str]]:
        """Strokes the client sent into placeholder [note_id], or None if it
        is not a recorded placeholder."""
        with self._lock:
            entries = self._read()
        return set(entries[note_id]) if note_id in entries else None


def _placeholders(db: FileNoteStore) -> _PlaceholderRegistry:
    return _PlaceholderRegistry(db.data_dir)


def _apply_client_timestamp(obj, value) -> bool:
    """
    EN: Keep the edit time a syncing client sends with note.json / page_N.ink
        instead of stamping the upload time. Stamping made every uploaded
        note look newer than edits made on the device right after the
        upload, so the next pull put the old version back over them.
        Returns False when no usable timestamp was sent.
    FR: Conserve l'heure de modification envoyée par le client plutôt que
        l'heure d'envoi, sinon la note paraît plus récente que les
        modifications faites juste après sur l'appareil.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    obj.updated_at = parsed
    return True


class InkFile(DAVNonCollection):
    """
    page_N.ink — données de strokes manuscrits d'une page.
    Format : JSON avec liste de strokes + points.
    Readable et writable.
    """

    def support_etag(self) -> bool:
        return True

    def get_etag(self) -> Optional[str]:
        return _safe_etag(
            "ink", self.page.note_id, self.page.page_number, self.page.updated_at
        )

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

        # EN: Like note.json, an ink page addressed to another note (the
        #     NexaNote client sends its note_id) must not overwrite the note
        #     this path resolved to through the slug/prefix fallbacks. The
        #     one exception is the client's own claimable placeholder (ink
        #     that lands before note.json).
        # FR: Un .ink destiné à une autre note est refusé (409), sauf pour le
        #     placeholder réclamable de ce client.
        payload_note_id = payload.get("note_id")
        if (
            isinstance(payload_note_id, str)
            and payload_note_id
            and payload_note_id != self.note.id
            and not _is_placeholder_for(self.db, self.note, payload_note_id)
        ):
            raise DAVError(
                HTTP_CONFLICT,
                context_info="page ink note_id does not match the note at this path",
            )

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
            if _apply_client_timestamp(self.page, payload.get("updated_at")):
                # The note's own timestamp arrives with note.json; only move
                # it forward if this drawing is newer.
                if self.page.updated_at > self.note.updated_at:
                    self.note.updated_at = self.page.updated_at
            else:
                self.page.touch()
                self.note.touch()
            self.db.save_page(self.page)
            self.db.save_note(self.note, save_pages=False)
            # Strokes the client sends into its own placeholder don't stop
            # the placeholder from being claimed; strokes drawn in it through
            # the app do (see _is_placeholder_for).
            _placeholders(self.db).record_strokes(
                self.note.id, [s.id for s in new_strokes]
            )
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

        EN: For PUT requests, transparently materialise missing parent
            collections (notebook + note) when their slug carries a valid
            id-prefix. This makes PUT idempotent and avoids spurious 409
            "PUT parent must be a collection" failures when a client skips
            (or partially fails) the MKCOL chain. Read methods stay strict
            and return None for unknown paths.
        FR: Pour les PUT, on matérialise à la volée les collections parentes
            manquantes (carnet/note) si leur slug porte un préfixe d'ID
            valide. Le PUT devient idempotent et n'aboutit plus à un 409
            « parent doit être une collection » quand le client a sauté
            (ou raté) la chaîne MKCOL. Les méthodes de lecture restent
            strictes.
        """
        path = path.rstrip("/") or "/"
        parts = [p for p in path.split("/") if p]
        auto_materialize = environ.get("REQUEST_METHOD", "").upper() == "PUT"

        # Racine
        if not parts:
            return RootCollection("/", environ, self.db)

        # Niveau carnet
        target_nb = _find_notebook_by_slug(self.db, parts[0])
        if target_nb is None:
            if auto_materialize and len(parts) >= 2:
                target_nb = _materialize_notebook(self.db, parts[0])
            if target_nb is None:
                return None

        nb_path = "/" + parts[0]

        if len(parts) == 1:
            return NotebookCollection(nb_path, environ, self.db, target_nb)

        # Niveau note
        target_note = _find_note_by_slug(self.db, target_nb.id, parts[1])
        if target_note is None:
            if auto_materialize and len(parts) >= 3:
                target_note = _materialize_note(self.db, target_nb, parts[1])
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


def _materialize_notebook(db: FileNoteStore, slug: str) -> Optional[Notebook]:
    """
    EN: Create a fresh notebook from a path slug. Returns None if the slug
        doesn't carry a usable id-prefix — we never invent ids for arbitrary
        names because that would mask typos as silent creations.
    FR: Crée un carnet à partir d'un slug de chemin. None si le slug n'a
        pas de préfixe d'ID exploitable.
    """
    title, id_prefix = _parse_slug(slug)
    if id_prefix is None and slug != _DEFAULT_NB_SLUG:
        return None
    try:
        notebook = Notebook(id=_id_with_prefix(id_prefix), name=title)
        db.save_notebook(notebook)
    except Exception:
        logger.exception("auto-materialize notebook failed: %s", slug)
        return None
    logger.info("Carnet matérialisé pour PUT : %s", title)
    return notebook


def _materialize_note(
    db: FileNoteStore, notebook: Notebook, slug: str
) -> Optional[Note]:
    """
    EN: Create a fresh note (with one empty page) from a path slug. Returns
        None unless the slug carries a usable id-prefix. The follow-up PUT
        of note.json will swap the placeholder id for the client's real id.
    FR: Crée une note (1 page vide) à partir d'un slug. None sans préfixe
        d'ID exploitable. Le PUT note.json ensuite remplace l'ID placeholder.
    """
    _, id_prefix = _parse_slug(slug)
    if id_prefix is None:
        return None
    title, _ = _parse_slug(slug)
    try:
        note = Note(
            id=_id_with_prefix(id_prefix),
            notebook_id=notebook.id,
            title=title,
        )
        note.add_page()
        _placeholders(db).add(note.id)
        db.save_note(note)
    except Exception:
        logger.exception("auto-materialize note failed: %s", slug)
        return None
    logger.info("Note matérialisée pour PUT : %s", title)
    return note
