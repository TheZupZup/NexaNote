"""
NexaNote — Clean Markdown export / Export Markdown propre.

EN: Writes each note as a `<title>.md` file containing only the markdown
    body — no YAML frontmatter, no NexaNote internals — so the output is
    directly usable in Obsidian and other plain-markdown tools. The internal
    NexaNote storage (notes/<id>.md with frontmatter, drawings/<id>.json,
    notebooks/<id>.yaml) is never touched.

FR: Écrit chaque note dans un fichier `<titre>.md` contenant uniquement le
    corps markdown — sans frontmatter ni métadonnées NexaNote — pour rester
    compatible Obsidian. Le stockage interne n'est jamais modifié.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from nexanote.models.note import Note
from nexanote.storage.file_store import FileNoteStore, _atomic_write

logger = logging.getLogger("nexanote.storage.export")


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

# Reserved across Windows + POSIX path separators + ASCII control chars.
_INVALID_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WS_RE = re.compile(r"\s+")
_MAX_LEN = 200
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FALLBACK = "untitled"


def sanitize_filename(name: str) -> str:
    """
    EN: Strip filesystem-unsafe characters, collapse whitespace, and trim
        trailing dots/spaces so the result is safe on Windows, macOS and
        Linux. Returns a fallback string when nothing usable remains.
    FR: Supprime les caractères interdits, fusionne les espaces, retire les
        points/espaces finaux. Renvoie une valeur de repli si vide.
    """
    cleaned = _INVALID_RE.sub("", name or "")
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > _MAX_LEN:
        cleaned = cleaned[:_MAX_LEN].rstrip(". ")
    if not cleaned:
        return _FALLBACK
    if cleaned.upper() in _RESERVED_NAMES:
        return f"_{cleaned}"
    return cleaned


def _unique_name(base: str, used: set[str]) -> str:
    """
    EN: Pick the first `<base>.md`, `<base> (2).md`, … that doesn't collide
        with any name in `used` (case-insensitive — Windows/macOS are not
        case-sensitive on the default filesystems).
    FR: Renvoie le premier nom libre dans la liste `used` (insensible à
        la casse, pour rester sûr sous Windows/macOS).
    """
    candidate = f"{base}.md"
    if candidate.lower() not in used:
        return candidate
    n = 2
    while True:
        candidate = f"{base} ({n}).md"
        if candidate.lower() not in used:
            return candidate
        n += 1


def _note_body(note: Note) -> str:
    """Concatenate every page's typed content, separated by a blank line."""
    parts = [
        page.typed_content.strip("\n")
        for page in note.pages
        if page.typed_content and page.typed_content.strip()
    ]
    if not parts:
        return ""
    return "\n\n".join(parts).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_note(
    note: Note,
    target_dir: Path,
    used_names: Optional[set[str]] = None,
) -> Path:
    """
    EN: Write `note` to `<target_dir>/<sanitized title>.md`. The file
        contains only the markdown body — no YAML frontmatter. Returns
        the path that was written.

        `used_names` is a case-insensitive set of `.md` filenames that
        should not be overwritten; on collision, a `(N)` suffix is added.
        Pass an empty set to start from scratch, or None to seed it from
        the directory's existing contents.
    FR: Écrit `note` en `<target_dir>/<titre nettoyé>.md`. Contenu = corps
        markdown uniquement, sans frontmatter. Renvoie le chemin écrit.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if used_names is None:
        used_names = {p.name.lower() for p in target_dir.glob("*.md")}

    base = sanitize_filename(note.title)
    name = _unique_name(base, used_names)
    used_names.add(name.lower())

    path = target_dir / name
    _atomic_write(path, _note_body(note).encode("utf-8"))
    return path


def export_all(
    store: FileNoteStore,
    target_dir: Path,
    include_archived: bool = False,
) -> list[Path]:
    """
    EN: Export every visible note in `store` as a clean Markdown file in
        `target_dir`. Soft-deleted notes are skipped (they are excluded by
        `list_notes` already). Returns the list of written file paths.
    FR: Exporte toutes les notes visibles vers `target_dir` en Markdown
        propre. Les notes en corbeille sont ignorées.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    used: set[str] = {p.name.lower() for p in target_dir.glob("*.md")}
    written: list[Path] = []
    for meta in store.list_notes(include_archived=include_archived):
        note = store.get_note(meta.id, load_pages=True)
        if note is None:
            continue
        try:
            written.append(export_note(note, target_dir, used))
        except OSError as exc:
            logger.warning(f"export failed for {note.title!r}: {exc}")
    return written


__all__ = [
    "export_all",
    "export_note",
    "sanitize_filename",
]
