"""
NexaNote — Plain Markdown storage backend / Backend Markdown brut.

EN: Alternative to ``FileNoteStore`` (the YAML-frontmatter backend) for users
    who want their notes to live as clean ``.md`` files directly editable in
    Obsidian and other plain-Markdown tools. The on-disk layout is:

        <data_dir>/
            notebooks/<notebook_id>.yaml      # Same as YAML mode (notebooks
                                              # don't show up in the user's
                                              # Markdown vault).
            notes/
                <Sanitized Title>.md          # Pure Markdown body — no
                                              # frontmatter, no NexaNote tags.
                <Sanitized Title>.json        # Sidecar metadata: id, tags,
                                              # dates, notebook_id, page meta.
            drawings/<note_id>.json           # Same as YAML mode (keyed by
                                              # the stable note id).

    Stable ids come from the sidecar JSON. ``.md`` files dropped in by the
    user with no sidecar (Obsidian users adding a file by hand) get a
    deterministic id derived from the filename so the API can address them
    until the user saves an edit through NexaNote (which writes a sidecar
    with a real UUID).

    The public method surface mirrors ``FileNoteStore`` so REST routes,
    the WebDAV provider and the sync engine all work unchanged.

FR: Backend alternatif à ``FileNoteStore`` pour stocker les notes comme
    fichiers ``.md`` propres + sidecars ``.json``. Compatible API avec
    ``FileNoteStore`` (REST, WebDAV, sync).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from nexanote.models.note import (
    InkStroke,
    Note,
    Notebook,
    NoteType,
    Page,
    SyncStatus,
)
from nexanote.storage.export import sanitize_filename
from nexanote.storage.file_store import (
    DRAWINGS_DIR,
    NOTEBOOKS_DIR,
    NOTES_DIR,
    _LOCKS,
    _atomic_write,
    _dict_to_stroke,
    _fmt_dt,
    _join_pages_body,
    _merge_metadata,
    _now,
    _parse_dt,
    _safe_id,
    _split_pages_body,
    _stroke_to_dict,
    deserialize_notebook,
    plain_md_id_from_stem,
    serialize_notebook,
    stem_from_plain_md_id,
    synthesize_plain_md_note,
)

if TYPE_CHECKING:
    from nexanote.storage.export import AutoExportConfig

logger = logging.getLogger("nexanote.storage.plain")

SIDECAR_SUFFIX = ".json"
PLAIN_SCHEMA_VERSION = 1


class PlainMarkdownNoteStore:
    """
    EN: Plain-Markdown backend with the same public surface as
        ``FileNoteStore``. Drop-in replacement for code that already
        consumes the YAML store via duck-typed calls.
    FR: Backend Markdown brut, même API publique que ``FileNoteStore``.
    """

    SCHEMA_VERSION = 3  # Distinct from FileNoteStore's layout version.

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

        # Lazy import — `export` reaches back into file_store helpers.
        from nexanote.storage.export import AutoExportConfig, AutoExporter
        if auto_export is None:
            auto_export = AutoExportConfig.from_env(self.data_dir / "export")
        self.auto_exporter = AutoExporter(auto_export)

    # ------------------------------------------------------------------
    # Compat shims expected by callers written against FileNoteStore.
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nexanote.db"

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _md_path(self, stem: str) -> Path:
        return self.notes_dir / f"{stem}.md"

    def _sidecar_path(self, stem: str) -> Path:
        return self.notes_dir / f"{stem}{SIDECAR_SUFFIX}"

    def _drawing_path(self, note_id: str) -> Path:
        return self.drawings_dir / f"{_safe_id(note_id)}.json"

    def _notebook_path(self, notebook_id: str) -> Path:
        return self.notebooks_dir / f"{_safe_id(notebook_id)}.yaml"

    # ------------------------------------------------------------------
    # ID ↔ stem resolution
    # ------------------------------------------------------------------

    def _stem_for_id(self, note_id: str) -> Optional[str]:
        """
        EN: Find the filename stem (`<Sanitized Title>`) for `note_id`.
            Reads sidecars to find the match. Falls back to the derived id
            for plain ``.md`` files dropped in by the user.
        FR: Trouve le stem du fichier pour `note_id`. Lit les sidecars,
            avec repli sur l'id dérivé pour les .md sans sidecar.
        """
        # Plain MD imports use the deterministic prefix scheme — recover the
        # stem directly when a file with that name exists.
        derived_stem = stem_from_plain_md_id(note_id)
        if derived_stem is not None:
            md = self._md_path(derived_stem)
            if md.exists() and not self._sidecar_path(derived_stem).exists():
                return derived_stem

        for sidecar in self.notes_dir.glob(f"*{SIDECAR_SUFFIX}"):
            data = self._safe_read_sidecar(sidecar)
            if data and data.get("id") == note_id:
                return sidecar.stem
        return None

    def _safe_read_sidecar(self, path: Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"unreadable sidecar {path}: {exc}")
            return None
        return data if isinstance(data, dict) else None

    def _pick_stem(self, base: str, owner_id: str) -> str:
        """
        EN: Pick a free filename stem for a note titled `base`. Reuses the
            slot already owned by `owner_id` if one exists; otherwise
            suffixes ` (N)` until a free slot is found.
        FR: Choisit un stem libre. Réutilise le slot déjà détenu par
            `owner_id` si possible, sinon suffixe ` (N)`.
        """
        candidate = base
        n = 1
        while True:
            md = self._md_path(candidate)
            sidecar = self._sidecar_path(candidate)

            if not md.exists() and not sidecar.exists():
                return candidate

            if sidecar.exists():
                data = self._safe_read_sidecar(sidecar)
                if data and data.get("id") == owner_id:
                    return candidate
            elif md.exists():
                # Plain MD without sidecar — owned by the derived id.
                if plain_md_id_from_stem(candidate) == owner_id:
                    return candidate

            n += 1
            candidate = f"{base} ({n})"

    # ------------------------------------------------------------------
    # Notebooks (delegated to YAML — they are not user-visible files)
    # ------------------------------------------------------------------

    def save_notebook(self, nb: Notebook) -> None:
        path = self._notebook_path(nb.id)
        with _LOCKS.get(path):
            _atomic_write(path, serialize_notebook(nb).encode("utf-8"))

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
    # Notes
    # ------------------------------------------------------------------

    def save_note(self, note: Note, save_pages: bool = True) -> None:
        """
        EN: Persist a Note as `<Title>.md` + `<Title>.json`. Renames the
            existing files when the title changes; reuses them otherwise.
            With `save_pages=False`, the existing `.md` body and drawings
            are kept and only the sidecar metadata is refreshed.
        FR: Persiste une note en `<titre>.md` + `<titre>.json`. Renomme
            si le titre change, conserve le corps si `save_pages=False`.
        """
        base = sanitize_filename(note.title)
        current_stem = self._stem_for_id(note.id)
        target_stem = self._pick_stem(base, owner_id=note.id)

        target_md = self._md_path(target_stem)
        target_sidecar = self._sidecar_path(target_stem)
        drawing_path = self._drawing_path(note.id)

        with _LOCKS.get(target_md):
            if not save_pages and current_stem:
                # Reload the existing pages/body so we don't drop them when
                # only metadata is being updated (mirrors FileNoteStore).
                existing = self._read_note_from_stem(current_stem, load_pages=True)
                if existing is not None and existing.pages:
                    note = _merge_metadata(note, existing)

            pages_text = {p.page_number: p.typed_content for p in note.pages}
            body = _join_pages_body(pages_text)
            sidecar = self._build_sidecar(note)

            # Write the new pair atomically before deleting the old one so a
            # crash mid-rename can't leave the note unreadable.
            _atomic_write(target_md, body.encode("utf-8"))
            _atomic_write(
                target_sidecar,
                json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8"),
            )

            # Remove the previous pair if the title moved us to a new stem.
            if current_stem and current_stem != target_stem:
                for p in (
                    self._md_path(current_stem),
                    self._sidecar_path(current_stem),
                ):
                    try:
                        p.unlink()
                    except FileNotFoundError:
                        pass

            # Drawings (per-note id, independent of the title-based stem).
            drawings = self._build_drawings(note)
            if drawings is None:
                try:
                    drawing_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write(
                    drawing_path,
                    json.dumps(drawings, ensure_ascii=False, indent=2).encode("utf-8"),
                )

        # Keep the auto-export mirror in sync (no-op when disabled).
        self.auto_exporter.export(note)

    def get_note(self, note_id: str, load_pages: bool = True) -> Optional[Note]:
        stem = self._stem_for_id(note_id)
        if stem is None:
            return None
        return self._read_note_from_stem(stem, load_pages=load_pages)

    def _read_note_from_stem(
        self, stem: str, load_pages: bool
    ) -> Optional[Note]:
        md_path = self._md_path(stem)
        if not md_path.exists():
            return None

        with _LOCKS.get(md_path):
            try:
                md_text = md_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.error(f"read md {md_path} failed: {exc}")
                return None

            sidecar = None
            sidecar_path = self._sidecar_path(stem)
            if sidecar_path.exists():
                sidecar = self._safe_read_sidecar(sidecar_path)

        if sidecar is not None:
            note = self._note_from_sidecar(sidecar, md_text)
        else:
            note = synthesize_plain_md_note(md_path, md_text)

        if not load_pages:
            note.pages = []
            return note

        # Drawings sidecar (separate file, keyed by note id)
        drawing_path = self._drawing_path(note.id)
        if drawing_path.exists():
            try:
                drawings = json.loads(drawing_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"unreadable drawings {drawing_path}: {exc}")
                drawings = None
            if drawings:
                self._attach_drawings(note, drawings)

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
        for md_path in self.notes_dir.glob("*.md"):
            sidecar_path = self._sidecar_path(md_path.stem)
            if sidecar_path.exists():
                data = self._safe_read_sidecar(sidecar_path)
                if data is None:
                    continue
                # Read the body lazily — only when required to build pages.
                note = self._note_from_sidecar(data, md_text="")
            else:
                try:
                    text = md_path.read_text(encoding="utf-8")
                except OSError as exc:
                    logger.warning(f"skip unreadable note {md_path}: {exc}")
                    continue
                note = synthesize_plain_md_note(md_path, text)
            note.pages = []  # listings stay metadata-only

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
        stem = self._stem_for_id(note_id)
        if stem is not None:
            md = self._md_path(stem)
            sidecar = self._sidecar_path(stem)
            with _LOCKS.get(md):
                for p in (md, sidecar):
                    try:
                        p.unlink()
                    except FileNotFoundError:
                        pass

        drawing = self._drawing_path(note_id)
        try:
            drawing.unlink()
        except FileNotFoundError:
            pass

        self.auto_exporter.remove(note_id)

    # ------------------------------------------------------------------
    # Pages CRUD (a page write rewrites the note — same as FileNoteStore)
    # ------------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        if not page.note_id:
            raise ValueError("Page is missing note_id")
        note = self.get_note(page.note_id, load_pages=True)
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
        note = self.get_note(note_id, load_pages=True)
        return list(note.pages) if note else []

    # ------------------------------------------------------------------
    # Strokes CRUD (scan-based, like FileNoteStore — production callers
    # route through save_page instead).
    # ------------------------------------------------------------------

    def save_stroke(self, stroke: InkStroke, page_id: str) -> None:
        for md_path in self.notes_dir.glob("*.md"):
            note = self._read_note_from_stem(md_path.stem, load_pages=True)
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
        for md_path in self.notes_dir.glob("*.md"):
            note = self._read_note_from_stem(md_path.stem, load_pages=True)
            if note is None:
                continue
            for page in note.pages:
                if page.id == page_id:
                    return list(page.strokes)
        return []

    def delete_stroke(self, stroke_id: str) -> None:
        for md_path in self.notes_dir.glob("*.md"):
            note = self._read_note_from_stem(md_path.stem, load_pages=True)
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

        for _ in self.list_notebooks(include_archived=False):
            notebooks += 1

        for md_path in self.notes_dir.glob("*.md"):
            note = self._read_note_from_stem(md_path.stem, load_pages=True)
            if note is None:
                continue
            if note.is_deleted:
                notes_deleted += 1
                continue
            if note.is_archived:
                continue
            notes += 1
            pages += len(note.pages)
            for page in note.pages:
                strokes += len(page.strokes)

        return {
            "notebooks": notebooks,
            "notes": notes,
            "notes_deleted": notes_deleted,
            "pages": pages,
            "strokes": strokes,
        }

    # ------------------------------------------------------------------
    # Sidecar serialization
    # ------------------------------------------------------------------

    def _build_sidecar(self, note: Note) -> dict:
        return {
            "schema": PLAIN_SCHEMA_VERSION,
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
            "pages": [
                {
                    "page_number": page.page_number,
                    "page_id": page.id,
                    "template": page.template,
                    "width_px": float(page.width_px),
                    "height_px": float(page.height_px),
                    "updated_at": _fmt_dt(page.updated_at),
                }
                for page in note.pages
            ],
        }

    def _note_from_sidecar(self, sidecar: dict, md_text: str) -> Note:
        pages_text = _split_pages_body(md_text) if md_text else {}
        pages: list[Page] = []
        for pm in sidecar.get("pages") or []:
            num = int(pm.get("page_number", 1))
            page = Page(
                id=pm.get("page_id") or f"{sidecar['id']}::p{num}",
                note_id=sidecar["id"],
                page_number=num,
                template=pm.get("template", "blank"),
                width_px=float(pm.get("width_px", 1404.0)),
                height_px=float(pm.get("height_px", 1872.0)),
                typed_content=pages_text.get(num, ""),
                created_at=_parse_dt(
                    pm.get("created_at", sidecar.get("created_at", _fmt_dt(_now())))
                ),
                updated_at=_parse_dt(
                    pm.get("updated_at", sidecar.get("updated_at", _fmt_dt(_now())))
                ),
            )
            pages.append(page)
        pages.sort(key=lambda p: p.page_number)

        return Note(
            id=sidecar["id"],
            notebook_id=sidecar.get("notebook_id"),
            title=sidecar.get("title", "Sans titre"),
            note_type=NoteType(sidecar.get("note_type", "typed")),
            tags=list(sidecar.get("tags") or []),
            is_pinned=bool(sidecar.get("is_pinned", False)),
            is_archived=bool(sidecar.get("is_archived", False)),
            is_deleted=bool(sidecar.get("is_deleted", False)),
            sync_status=SyncStatus(
                sidecar.get("sync_status", SyncStatus.LOCAL_ONLY.value)
            ),
            created_at=_parse_dt(sidecar.get("created_at", _fmt_dt(_now()))),
            updated_at=_parse_dt(sidecar.get("updated_at", _fmt_dt(_now()))),
            pages=pages,
        )

    # ------------------------------------------------------------------
    # Drawings serialization
    # ------------------------------------------------------------------

    def _build_drawings(self, note: Note) -> Optional[dict]:
        if not any(p.strokes for p in note.pages):
            return None
        return {
            "schema": 1,
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

    def _attach_drawings(self, note: Note, drawings: dict) -> None:
        by_num = {
            int(p.get("page_number", 1)): [
                _dict_to_stroke(s) for s in p.get("strokes") or []
            ]
            for p in drawings.get("pages") or []
        }
        for page in note.pages:
            page.strokes = by_num.get(page.page_number, [])


__all__ = [
    "PlainMarkdownNoteStore",
    "PLAIN_SCHEMA_VERSION",
    "SIDECAR_SUFFIX",
]
