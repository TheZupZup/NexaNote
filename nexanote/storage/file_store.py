"""
NexaNote — File-based storage layer / Couche de stockage fichier.

EN: Replaces the legacy SQLite database with a plain-file layout, modeled
    after Obsidian. The on-disk structure is:

        <data_dir>/
            notebooks/<notebook_id>.yaml      # Notebook metadata (YAML)
            notes/<note_id>.md                # Markdown + YAML frontmatter
            drawings/<note_id>.json           # Stylus strokes (one file/note)

    The public API mirrors NexaNoteDB so the rest of the codebase
    (REST API, WebDAV provider, sync engine) is a drop-in replacement.

FR: Remplace l'ancienne base SQLite par une arborescence de fichiers,
    inspirée d'Obsidian. La structure sur disque est :

        <data_dir>/
            notebooks/<notebook_id>.yaml      # Métadonnées du carnet (YAML)
            notes/<note_id>.md                # Markdown + frontmatter YAML
            drawings/<note_id>.json           # Traits stylet (1 fichier/note)

    L'API publique imite NexaNoteDB pour rester un remplacement direct.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from nexanote.models.note import (
    InkStroke,
    Note,
    Notebook,
    NoteType,
    Page,
    Point,
    SyncStatus,
)

if TYPE_CHECKING:
    # EN: `export` imports this module, so only resolve the type at check time
    #     to keep runtime imports cycle-free.
    from nexanote.storage.export import AutoExportConfig  # noqa: F401

logger = logging.getLogger("nexanote.storage.file")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOTES_DIR = "notes"
DRAWINGS_DIR = "drawings"
NOTEBOOKS_DIR = "notebooks"

FRONTMATTER_DELIM = "---"
PAGE_MARKER_RE = re.compile(r"^<!--\s*nexanote:page\s+(\d+)\s*-->\s*$", re.MULTILINE)
DRAWING_SCHEMA_VERSION = 1

# EN: Synthetic id prefix used for plain Markdown files (no frontmatter).
#     The remainder is URL-safe base64 of the file stem so the id is stable
#     across reads and the original filename can be recovered without an
#     extra index. The chars used by the prefix and base64 alphabet all
#     pass through `_safe_id()` unchanged.
# FR: Préfixe d'id pour les fichiers Markdown bruts (sans frontmatter). Le
#     reste est le stem en base64 url-safe : id stable et nom de fichier
#     récupérable sans index annexe.
PLAIN_MD_ID_PREFIX = "md."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value) -> datetime:
    """Parse an ISO datetime string (or pass through datetime)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"cannot parse datetime from {type(value).__name__}")


def _fmt_dt(dt: datetime) -> str:
    return dt.isoformat()


def _atomic_write(path: Path, data: bytes) -> None:
    """
    EN: Write `data` to `path` atomically (tmp file + os.replace).
        Avoids a partial file if the process is killed mid-write.
    FR: Écrit `data` dans `path` de manière atomique (tmp + os.replace).
        Évite un fichier corrompu si le processus est tué pendant l'écriture.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Lock registry (per-path RLock for thread-safety inside the process)
# ---------------------------------------------------------------------------

class _LockRegistry:
    """
    EN: Hands out one RLock per absolute path. Cheap and bounded — locks
        outlive their callers but are reused across requests.
    FR: Distribue une RLock par chemin absolu. Léger et borné — les locks
        survivent aux appels mais sont réutilisés.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def get(self, path: Path) -> threading.RLock:
        key = str(path)
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock


_LOCKS = _LockRegistry()


# ---------------------------------------------------------------------------
# Frontmatter / Markdown serialization
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[dict, str]:
    """
    EN: Parse `---\\n<YAML>\\n---\\n<body>`. Returns ({}, text) if no
        frontmatter is present.
    FR: Parse `---\\n<YAML>\\n---\\n<body>`. Retourne ({}, text) sans
        frontmatter.
    """
    if not text.startswith(FRONTMATTER_DELIM):
        return {}, text

    # Allow either \n or \r\n after the opening delimiter.
    rest = text[len(FRONTMATTER_DELIM):].lstrip("\r\n")
    end_marker = f"\n{FRONTMATTER_DELIM}"
    end_idx = rest.find(end_marker)
    if end_idx == -1:
        return {}, text

    yaml_text = rest[:end_idx]
    body_start = end_idx + len(end_marker)
    body = rest[body_start:].lstrip("\r\n")

    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"YAML frontmatter parse failed: {exc}")
        return {}, text

    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def _join_frontmatter(meta: dict, body: str) -> str:
    yaml_text = yaml.safe_dump(
        meta,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    body_part = body if body.endswith("\n") else body + "\n"
    return f"{FRONTMATTER_DELIM}\n{yaml_text}\n{FRONTMATTER_DELIM}\n\n{body_part}"


def _split_pages_body(body: str) -> dict[int, str]:
    """
    EN: Extract per-page content from the markdown body.
        - With no markers: the entire body belongs to page 1.
        - With `<!-- nexanote:page N -->` markers: split accordingly.
    FR: Extrait le contenu page par page depuis le corps markdown.
    """
    matches = list(PAGE_MARKER_RE.finditer(body))
    if not matches:
        return {1: body.rstrip("\n")}

    pages: dict[int, str] = {}

    # Anything before the first marker is dropped on round-trip — but to be
    # safe, prepend it to the first marker's page so users never lose text.
    first = matches[0]
    if first.start() > 0:
        prefix = body[: first.start()].strip("\n")
    else:
        prefix = ""

    for i, match in enumerate(matches):
        num = int(match.group(1))
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        chunk = body[content_start:content_end].strip("\n")
        if i == 0 and prefix:
            chunk = (prefix + "\n\n" + chunk).strip("\n") if chunk else prefix
        pages[num] = chunk

    return pages


def _join_pages_body(pages_text: dict[int, str]) -> str:
    """
    EN: Inverse of `_split_pages_body`. Single-page notes get a clean body
        with no markers, so they remain Obsidian-friendly.
    FR: Inverse de `_split_pages_body`. Note à 1 page = corps propre sans
        marqueur, pour rester compatible Obsidian.
    """
    if not pages_text:
        return ""
    if len(pages_text) == 1:
        only = next(iter(pages_text.values()))
        return only.rstrip("\n") + "\n"

    chunks: list[str] = []
    for num in sorted(pages_text):
        chunks.append(f"<!-- nexanote:page {num} -->\n{pages_text[num].rstrip(chr(10))}")
    return "\n\n".join(chunks).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Note (file) <-> dataclass
# ---------------------------------------------------------------------------

def serialize_note(note: Note) -> tuple[str, Optional[dict]]:
    """
    EN: Serialize a `Note` to:
          - markdown text (frontmatter + per-page body)
          - drawings dict (or None if no strokes anywhere)
    FR: Sérialise une `Note` en :
          - markdown (frontmatter + corps page par page)
          - dict de strokes (ou None si aucun trait)
    """
    pages_meta = []
    pages_text: dict[int, str] = {}
    has_strokes = False

    for page in note.pages:
        pages_meta.append({
            "page_number": page.page_number,
            "page_id": page.id,
            "template": page.template,
            "width_px": float(page.width_px),
            "height_px": float(page.height_px),
            "updated_at": _fmt_dt(page.updated_at),
        })
        pages_text[page.page_number] = page.typed_content
        if page.strokes:
            has_strokes = True

    frontmatter = {
        "id": note.id,
        "title": note.title,
        "note_type": note.note_type.value,
        "notebook_id": note.notebook_id,
        "tags": list(note.tags),
        "is_pinned": note.is_pinned,
        "is_archived": note.is_archived,
        "is_deleted": note.is_deleted,
        "sync_status": note.sync_status.value,
        "created_at": _fmt_dt(note.created_at),
        "updated_at": _fmt_dt(note.updated_at),
        "pages": pages_meta,
    }
    md_text = _join_frontmatter(frontmatter, _join_pages_body(pages_text))

    drawings: Optional[dict] = None
    if has_strokes:
        drawings = {
            "schema": DRAWING_SCHEMA_VERSION,
            "note_id": note.id,
            "updated_at": _fmt_dt(note.updated_at),
            "pages": [
                {
                    "page_number": p.page_number,
                    "page_id": p.id,
                    "updated_at": _fmt_dt(p.updated_at),
                    "strokes": [_stroke_to_dict(s) for s in p.strokes],
                }
                for p in note.pages
                if p.strokes
            ],
        }
    return md_text, drawings


def deserialize_note(md_text: str, drawings: Optional[dict]) -> Optional[Note]:
    """Rebuild a `Note` from its on-disk markdown + drawings JSON."""
    meta, body = _split_frontmatter(md_text)
    if not meta or "id" not in meta:
        return None

    pages_text = _split_pages_body(body)
    pages_meta = meta.get("pages") or []

    # Strokes per page_number
    strokes_by_page: dict[int, list[InkStroke]] = {}
    if drawings:
        for page_data in drawings.get("pages") or []:
            num = int(page_data.get("page_number", 1))
            strokes_by_page[num] = [
                _dict_to_stroke(s) for s in page_data.get("strokes") or []
            ]

    pages: list[Page] = []
    for pm in pages_meta:
        num = int(pm.get("page_number", 1))
        page = Page(
            id=pm.get("page_id") or pm.get("id") or "",
            note_id=meta["id"],
            page_number=num,
            template=pm.get("template", "blank"),
            width_px=float(pm.get("width_px", 1404.0)),
            height_px=float(pm.get("height_px", 1872.0)),
            typed_content=pages_text.get(num, ""),
            strokes=strokes_by_page.get(num, []),
            created_at=_parse_dt(pm.get("created_at", meta.get("created_at", _fmt_dt(_now())))),
            updated_at=_parse_dt(pm.get("updated_at", meta.get("updated_at", _fmt_dt(_now())))),
        )
        if not page.id:
            # Backfill a stable-ish id derived from the note id + page number.
            page.id = f"{meta['id']}::p{num}"
        pages.append(page)

    pages.sort(key=lambda p: p.page_number)

    return Note(
        id=meta["id"],
        notebook_id=meta.get("notebook_id"),
        title=meta.get("title", "Sans titre"),
        note_type=NoteType(meta.get("note_type", "typed")),
        tags=list(meta.get("tags") or []),
        is_pinned=bool(meta.get("is_pinned", False)),
        is_archived=bool(meta.get("is_archived", False)),
        is_deleted=bool(meta.get("is_deleted", False)),
        sync_status=SyncStatus(meta.get("sync_status", SyncStatus.LOCAL_ONLY.value)),
        created_at=_parse_dt(meta.get("created_at", _fmt_dt(_now()))),
        updated_at=_parse_dt(meta.get("updated_at", _fmt_dt(_now()))),
        pages=pages,
    )


def _stroke_to_dict(stroke: InkStroke) -> dict:
    return {
        "id": stroke.id,
        "color": stroke.color,
        "width": float(stroke.width),
        "tool": stroke.tool,
        "created_at": _fmt_dt(stroke.created_at),
        "points": [
            {"x": float(p.x), "y": float(p.y), "pressure": float(p.pressure), "ts": int(p.timestamp_ms)}
            for p in stroke.points
        ],
    }


def _dict_to_stroke(data: dict) -> InkStroke:
    points = [
        Point(
            x=float(p.get("x", 0.0)),
            y=float(p.get("y", 0.0)),
            pressure=float(p.get("pressure", 0.5)),
            timestamp_ms=int(p.get("ts", p.get("timestamp_ms", 0))),
        )
        for p in data.get("points") or []
    ]
    return InkStroke(
        id=data["id"],
        color=data.get("color", "#000000"),
        width=float(data.get("width", 2.0)),
        tool=data.get("tool", "pen"),
        points=points,
        created_at=_parse_dt(data.get("created_at", _fmt_dt(_now()))),
    )


# ---------------------------------------------------------------------------
# Notebook (file) <-> dataclass
# ---------------------------------------------------------------------------

def serialize_notebook(nb: Notebook) -> str:
    payload = {
        "id": nb.id,
        "parent_id": nb.parent_id,
        "name": nb.name,
        "description": nb.description,
        "color": nb.color,
        "icon": nb.icon,
        "is_archived": nb.is_archived,
        "sync_status": nb.sync_status.value,
        "created_at": _fmt_dt(nb.created_at),
        "updated_at": _fmt_dt(nb.updated_at),
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def deserialize_notebook(text: str) -> Optional[Notebook]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"notebook YAML parse failed: {exc}")
        return None
    if not isinstance(data, dict) or "id" not in data:
        return None
    return Notebook(
        id=data["id"],
        parent_id=data.get("parent_id"),
        name=data.get("name", "Nouveau carnet"),
        description=data.get("description", ""),
        color=data.get("color", "#6366f1"),
        icon=data.get("icon", "notebook"),
        is_archived=bool(data.get("is_archived", False)),
        sync_status=SyncStatus(data.get("sync_status", SyncStatus.LOCAL_ONLY.value)),
        created_at=_parse_dt(data.get("created_at", _fmt_dt(_now()))),
        updated_at=_parse_dt(data.get("updated_at", _fmt_dt(_now()))),
    )


# ---------------------------------------------------------------------------
# FileNoteStore — main public class
# ---------------------------------------------------------------------------

class FileNoteStore:
    """
    EN: File-backed note store. Public API matches the legacy NexaNoteDB
        so consumers (API routes, WebDAV provider, sync engine) work
        unchanged.
    FR: Stockage de notes basé fichier. L'API publique colle à l'ancien
        NexaNoteDB pour que l'API REST, le provider WebDAV et le moteur
        de sync n'aient rien à changer.
    """

    SCHEMA_VERSION = 2  # Bumped when the on-disk layout changes.

    def __init__(
        self,
        data_dir: Path,
        auto_export: Optional["AutoExportConfig"] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.notes_dir = self.data_dir / NOTES_DIR
        self.drawings_dir = self.data_dir / DRAWINGS_DIR
        self.notebooks_dir = self.data_dir / NOTEBOOKS_DIR
        for d in (self.notes_dir, self.drawings_dir, self.notebooks_dir):
            d.mkdir(parents=True, exist_ok=True)

        # EN: Lazy import — `export` depends on this module for `_atomic_write`,
        #     so doing it at module top would create a circular import.
        # FR: Import retardé — évite l'import circulaire avec `export`.
        from nexanote.storage.export import AutoExportConfig, AutoExporter
        if auto_export is None:
            auto_export = AutoExportConfig.from_env(self.data_dir / "export")
        self.auto_exporter = AutoExporter(auto_export)

    # ------------------------------------------------------------------
    # Compatibility shims for code paths that used to read db.db_path
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        """
        EN: Legacy accessor. Some helpers (e.g. /storage route, sync config
            persistence) used `db.db_path.parent` to find the data dir.
            Return a synthetic path inside `data_dir` so those callers keep
            working without an explicit migration.
        FR: Accesseur de compat. Renvoie un chemin synthétique dans le
            data_dir pour les anciens appelants qui faisaient
            `db.db_path.parent`.
        """
        return self.data_dir / "nexanote.db"

    def close(self) -> None:
        """No persistent connection to close — kept for API parity."""
        return None

    # ------------------------------------------------------------------
    # File path helpers
    # ------------------------------------------------------------------

    def _note_path(self, note_id: str) -> Path:
        stem = stem_from_plain_md_id(note_id)
        if stem is not None:
            return self.notes_dir / f"{stem}.md"
        return self.notes_dir / f"{_safe_id(note_id)}.md"

    def _drawing_path(self, note_id: str) -> Path:
        return self.drawings_dir / f"{_safe_id(note_id)}.json"

    def _notebook_path(self, notebook_id: str) -> Path:
        return self.notebooks_dir / f"{_safe_id(notebook_id)}.yaml"

    # ------------------------------------------------------------------
    # Notebooks CRUD
    # ------------------------------------------------------------------

    def save_notebook(self, nb: Notebook) -> None:
        path = self._notebook_path(nb.id)
        with _LOCKS.get(path):
            data = serialize_notebook(nb).encode("utf-8")
            _atomic_write(path, data)

    def get_notebook(self, notebook_id: str) -> Optional[Notebook]:
        path = self._notebook_path(notebook_id)
        if not path.exists():
            return None
        with _LOCKS.get(path):
            try:
                return deserialize_notebook(path.read_text(encoding="utf-8"))
            except OSError as exc:
                logger.error(f"read notebook {path} failed: {exc}")
                return None

    def list_notebooks(self, include_archived: bool = False) -> list[Notebook]:
        out: list[Notebook] = []
        for path in sorted(self.notebooks_dir.glob("*.yaml")):
            try:
                nb = deserialize_notebook(path.read_text(encoding="utf-8"))
            except OSError as exc:
                logger.warning(f"skip unreadable notebook {path}: {exc}")
                continue
            if nb is None:
                continue
            if not include_archived and nb.is_archived:
                continue
            out.append(nb)
        out.sort(key=lambda nb: nb.name.lower())
        return out

    def delete_notebook(self, notebook_id: str) -> None:
        path = self._notebook_path(notebook_id)
        with _LOCKS.get(path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    # Notes CRUD
    # ------------------------------------------------------------------

    def save_note(self, note: Note, save_pages: bool = True) -> None:
        """
        EN: Persist a Note. If `save_pages=False` (used by routes after
            updating only metadata), we preserve the existing per-page
            body and the existing drawings file.
        FR: Persiste une Note. Si `save_pages=False`, on conserve le corps
            par page et le fichier drawings existants.
        """
        note_path = self._note_path(note.id)
        drawing_path = self._drawing_path(note.id)

        with _LOCKS.get(note_path):
            if not save_pages and note_path.exists():
                # Reload existing pages so we don't blow them away on a
                # metadata-only update.
                existing = self._read_note(note.id, load_pages=True)
                if existing is not None and existing.pages:
                    note = _merge_metadata(note, existing)

            md_text, drawings = serialize_note(note)
            _atomic_write(note_path, md_text.encode("utf-8"))

            if drawings is None:
                # No strokes anywhere → drop drawings file if present.
                try:
                    drawing_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write(
                    drawing_path,
                    json.dumps(drawings, ensure_ascii=False, indent=2).encode("utf-8"),
                )

        # EN: Mirror the saved note as clean Markdown if auto-export is on.
        #     Runs outside the per-file lock so a slow target FS can't block
        #     concurrent writes to other notes; the exporter uses its own lock
        #     to keep the index consistent.
        # FR: Réplique la note sauvée en Markdown propre si l'export auto est
        #     activé. Hors du lock fichier pour ne pas bloquer d'autres écritures.
        self.auto_exporter.export(note)

    def get_note(self, note_id: str, load_pages: bool = True) -> Optional[Note]:
        return self._read_note(note_id, load_pages=load_pages)

    def _read_note(self, note_id: str, load_pages: bool) -> Optional[Note]:
        note_path = self._note_path(note_id)
        if not note_path.exists():
            return None
        with _LOCKS.get(note_path):
            try:
                md_text = note_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error(f"read note {note_path} failed: {exc}")
                return None

            drawings = None
            drawing_path = self._drawing_path(note_id)
            if load_pages and drawing_path.exists():
                try:
                    drawings = json.loads(drawing_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(f"unreadable drawings {drawing_path}: {exc}")
                    drawings = None

        note = deserialize_note(md_text, drawings)
        if note is None:
            # Plain Markdown (no NexaNote frontmatter) — surface it as a note
            # without rewriting the file. Internal storage stays untouched
            # until the user explicitly saves an edit through NexaNote.
            note = synthesize_plain_md_note(note_path, md_text)
        if not load_pages:
            note.pages = []
        return note

    def list_notes(
        self,
        notebook_id: Optional[str] = None,
        include_deleted: bool = False,
        include_archived: bool = False,
        search_title: Optional[str] = None,
    ) -> list[Note]:
        out: list[Note] = []
        needle = search_title.lower() if search_title else None
        for path in self.notes_dir.glob("*.md"):
            try:
                md_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(f"skip unreadable note {path}: {exc}")
                continue
            note = deserialize_note(md_text, None)
            if note is None:
                note = synthesize_plain_md_note(path, md_text)
            note.pages = []  # listings are metadata-only

            if not include_deleted and note.is_deleted:
                continue
            if not include_archived and note.is_archived:
                continue
            if notebook_id is not None and note.notebook_id != notebook_id:
                continue
            if needle and needle not in note.title.lower():
                continue
            out.append(note)

        out.sort(key=lambda n: n.updated_at, reverse=True)
        return out

    def delete_note_permanent(self, note_id: str) -> None:
        """Permanent delete (purge from trash)."""
        note_path = self._note_path(note_id)
        drawing_path = self._drawing_path(note_id)
        with _LOCKS.get(note_path):
            for p in (note_path, drawing_path):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass

        # Mirror the purge into the auto-exported directory if enabled.
        self.auto_exporter.remove(note_id)

    # ------------------------------------------------------------------
    # Pages CRUD (kept for API parity — a page write rewrites the note)
    # ------------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        if not page.note_id:
            raise ValueError("Page is missing note_id")
        note = self._read_note(page.note_id, load_pages=True)
        if note is None:
            raise KeyError(f"unknown note {page.note_id}")
        replaced = False
        for i, existing in enumerate(note.pages):
            if existing.page_number == page.page_number:
                note.pages[i] = page
                replaced = True
                break
        if not replaced:
            note.pages.append(page)
        note.pages.sort(key=lambda p: p.page_number)
        self.save_note(note, save_pages=True)

    def list_pages(self, note_id: str) -> list[Page]:
        note = self._read_note(note_id, load_pages=True)
        return list(note.pages) if note else []

    # ------------------------------------------------------------------
    # Strokes CRUD
    # ------------------------------------------------------------------

    def save_stroke(self, stroke: InkStroke, page_id: str) -> None:
        # Find the owning note by scanning pages — strokes are addressed
        # via page_id which doesn't index a file directly. Mirrors the
        # legacy DB API; production callers route through save_page().
        for note_path in self.notes_dir.glob("*.md"):
            note = self._read_note(note_path.stem, load_pages=True)
            if note is None:
                continue
            for page in note.pages:
                if page.id == page_id:
                    page.strokes = [s for s in page.strokes if s.id != stroke.id]
                    page.strokes.append(stroke)
                    self.save_note(note, save_pages=True)
                    return
        raise KeyError(f"unknown page {page_id}")

    def list_strokes(self, page_id: str) -> list[InkStroke]:
        for note_path in self.notes_dir.glob("*.md"):
            note = self._read_note(note_path.stem, load_pages=True)
            if note is None:
                continue
            for page in note.pages:
                if page.id == page_id:
                    return list(page.strokes)
        return []

    def delete_stroke(self, stroke_id: str) -> None:
        for note_path in self.notes_dir.glob("*.md"):
            note = self._read_note(note_path.stem, load_pages=True)
            if note is None:
                continue
            changed = False
            for page in note.pages:
                before = len(page.strokes)
                page.strokes = [s for s in page.strokes if s.id != stroke_id]
                if len(page.strokes) != before:
                    changed = True
            if changed:
                self.save_note(note, save_pages=True)
                return

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        notebooks = pages = strokes = 0
        notes = notes_deleted = 0

        for nb in self.list_notebooks(include_archived=False):
            notebooks += 1

        for path in self.notes_dir.glob("*.md"):
            try:
                md_text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            note = deserialize_note(md_text, None)
            if note is None:
                note = synthesize_plain_md_note(path, md_text)
            if note.is_deleted:
                notes_deleted += 1
                continue
            if note.is_archived:
                continue
            # Reload with pages/strokes for the body counters. Plain MDs don't
            # have a drawings sidecar, so this is a no-op for them.
            full = self._read_note(note.id, load_pages=True) or note
            notes += 1
            pages += len(full.pages)
            for page in full.pages:
                strokes += len(page.strokes)

        return {
            "notebooks": notebooks,
            "notes": notes,
            "notes_deleted": notes_deleted,
            "pages": pages,
            "strokes": strokes,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_BASE64_URLSAFE_RE = re.compile(r"[A-Za-z0-9_\-]+")


def _safe_id(value: str) -> str:
    """Sanitize an id for filesystem use. UUIDs already match — this is defensive."""
    if not value:
        return "_"
    sanitized = _SAFE_ID_RE.sub("_", value)
    return sanitized[:160]  # generous cap


def plain_md_id_from_stem(stem: str) -> str:
    """Build the synthetic note id used to address a plain `.md` file."""
    encoded = base64.urlsafe_b64encode(stem.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{PLAIN_MD_ID_PREFIX}{encoded}"


def stem_from_plain_md_id(note_id: str) -> Optional[str]:
    """Inverse of `plain_md_id_from_stem`. Returns None for non-plain ids."""
    if not note_id or not note_id.startswith(PLAIN_MD_ID_PREFIX):
        return None
    encoded = note_id[len(PLAIN_MD_ID_PREFIX):]
    if not encoded or not _BASE64_URLSAFE_RE.fullmatch(encoded):
        return None
    pad = (-len(encoded)) % 4
    try:
        return base64.urlsafe_b64decode(encoded + "=" * pad).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def synthesize_plain_md_note(path: Path, md_text: str) -> Note:
    """
    EN: Build a Note from a plain Markdown file (no NexaNote frontmatter).
        Title comes from the filename, body is the raw file content, and
        timestamps are derived from the filesystem so external edits show
        up on the next listing.
    FR: Construit une Note à partir d'un fichier Markdown brut. Le titre
        vient du nom de fichier, le corps est le contenu, et les dates
        viennent du système de fichiers.
    """
    try:
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    except OSError:
        created = updated = _now()

    note_id = plain_md_id_from_stem(path.stem)
    page = Page(
        id=f"{note_id}::p1",
        note_id=note_id,
        page_number=1,
        template="blank",
        typed_content=md_text.rstrip("\n"),
        created_at=created,
        updated_at=updated,
    )
    return Note(
        id=note_id,
        title=path.stem,
        note_type=NoteType.TYPED,
        sync_status=SyncStatus.LOCAL_ONLY,
        created_at=created,
        updated_at=updated,
        pages=[page],
    )


def _merge_metadata(meta_source: Note, with_pages: Note) -> Note:
    """
    EN: Used when `save_note(save_pages=False)` is called — overlay the
        metadata fields from `meta_source` onto the existing note (which
        carries the canonical pages/strokes).
    FR: Surcharge les métadonnées de `meta_source` sur la note existante
        qui porte les pages/strokes canoniques.
    """
    with_pages.title = meta_source.title
    with_pages.notebook_id = meta_source.notebook_id
    with_pages.tags = list(meta_source.tags)
    with_pages.note_type = meta_source.note_type
    with_pages.is_pinned = meta_source.is_pinned
    with_pages.is_archived = meta_source.is_archived
    with_pages.is_deleted = meta_source.is_deleted
    with_pages.sync_status = meta_source.sync_status
    with_pages.updated_at = meta_source.updated_at
    return with_pages


__all__ = [
    "FileNoteStore",
    "deserialize_note",
    "serialize_note",
    "deserialize_notebook",
    "serialize_notebook",
    "NOTES_DIR",
    "DRAWINGS_DIR",
    "NOTEBOOKS_DIR",
]
