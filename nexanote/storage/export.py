"""
NexaNote — Clean Markdown export / Export Markdown propre.

EN: Writes each note as a `<title>.md` file containing only the markdown
    body — no YAML frontmatter, no NexaNote internals — so the output is
    directly usable in Obsidian and other plain-markdown tools. The internal
    NexaNote storage (notes/<id>.md with frontmatter, drawings/<id>.json,
    notebooks/<id>.yaml) is never touched.

    Two entry points:
      * `export_all` / `export_note`: one-shot batch export (manual).
      * `AutoExporter`: per-note hook driven by the file store so saves,
        creates, title changes and sync pulls keep an Obsidian-friendly
        mirror in sync without manual intervention.

FR: Écrit chaque note dans un fichier `<titre>.md` contenant uniquement le
    corps markdown — sans frontmatter ni métadonnées NexaNote — pour rester
    compatible Obsidian. Le stockage interne n'est jamais modifié.

    Deux points d'entrée :
      * `export_all` / `export_note` : export ponctuel (manuel).
      * `AutoExporter` : hook par note, déclenché par le store fichier, pour
        garder un miroir Obsidian à jour sans intervention manuelle.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from nexanote.models.note import Note

if TYPE_CHECKING:
    from nexanote.storage.file_store import FileNoteStore

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
# Public API — manual / batch export
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
    from nexanote.storage.file_store import _atomic_write

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
    store: "FileNoteStore",
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


# ---------------------------------------------------------------------------
# Automatic export — driven by the file store
# ---------------------------------------------------------------------------

# EN: Env vars consumed by `AutoExportConfig.from_env`. The feature is opt-in:
#     when the flag is missing or set to a falsy value we never write to disk.
# FR: Variables d'environnement lues par `AutoExportConfig.from_env`.
#     Fonctionnalité désactivée par défaut.
ENV_AUTO_EXPORT = "NEXANOTE_AUTO_EXPORT_MARKDOWN"
ENV_EXPORT_DIR = "NEXANOTE_MARKDOWN_EXPORT_DIR"

# EN: Index sidecar that lets the auto-exporter remember which file backs
#     each note. Without it we couldn't rename on title change or clean up
#     after a soft delete without scanning every file's body.
# FR: Index annexe qui mémorise le fichier exporté pour chaque note.
INDEX_FILE = ".nexanote_export_index.json"
INDEX_VERSION = 1

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_truthy(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() in _TRUE_VALUES


@dataclass
class AutoExportConfig:
    """
    EN: Per-store configuration for the automatic Markdown export.
        Disabled by default — the user must explicitly opt in via env var or
        constructor argument so internal storage stays the only source of
        truth for users who don't care about an Obsidian mirror.
    FR: Configuration par store pour l'export Markdown automatique.
        Désactivé par défaut — opt-in via env ou argument.
    """

    enabled: bool = False
    target_dir: Optional[Path] = None

    @classmethod
    def from_env(
        cls,
        default_dir: Path,
        env: Optional[dict] = None,
    ) -> "AutoExportConfig":
        """
        EN: Build a config from `NEXANOTE_AUTO_EXPORT_MARKDOWN` and
            `NEXANOTE_MARKDOWN_EXPORT_DIR`. `default_dir` is used when the
            export-dir variable is unset.
        FR: Construit une config depuis les variables d'environnement.
        """
        source = env if env is not None else os.environ
        enabled = _env_truthy(source.get(ENV_AUTO_EXPORT))
        dir_value = source.get(ENV_EXPORT_DIR)
        target = Path(dir_value).expanduser() if dir_value else Path(default_dir)
        return cls(enabled=enabled, target_dir=target)


class AutoExporter:
    """
    EN: Mirrors managed notes as clean `<title>.md` files in a target
        directory. Owns a small JSON index so it can:
          * overwrite the same file when a note is saved repeatedly,
          * delete the old file and write a new one when the title changes,
          * suffix `(N)` on filename collisions across notes,
          * clean up after soft delete / archive / hard delete.

        Failures never propagate — auto-export is an extra, not a guarantee.

    FR: Maintient un miroir des notes en `<titre>.md` propres dans un
        dossier cible. Utilise un petit index JSON pour gérer renommages,
        suppressions et collisions sans casser l'enregistrement principal.
    """

    def __init__(self, config: AutoExportConfig) -> None:
        self.config = config
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.target_dir is not None)

    @property
    def target_dir(self) -> Path:
        # Caller must not invoke this when disabled.
        assert self.config.target_dir is not None
        return self.config.target_dir

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _index_path(self) -> Path:
        return self.target_dir / INDEX_FILE

    def _load_index(self) -> dict[str, str]:
        path = self._index_path()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"auto-export index unreadable ({path}): {exc}")
            return {}
        mapping = payload.get("by_note_id") if isinstance(payload, dict) else None
        if not isinstance(mapping, dict):
            return {}
        # Defensive: drop entries that aren't str→str.
        return {
            str(k): str(v)
            for k, v in mapping.items()
            if isinstance(k, str) and isinstance(v, str)
        }

    def _save_index(self, by_note_id: dict[str, str]) -> None:
        from nexanote.storage.file_store import _atomic_write

        path = self._index_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": INDEX_VERSION, "by_note_id": by_note_id}
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            _atomic_write(path, data)
        except OSError as exc:
            logger.warning(f"auto-export index write failed ({path}): {exc}")

    # ------------------------------------------------------------------
    # Public API used by FileNoteStore
    # ------------------------------------------------------------------

    def export(self, note: Note) -> Optional[Path]:
        """
        EN: Mirror `note` as a clean `.md` file. Returns the written path,
            or None when auto-export is disabled / the note is excluded /
            the write fails.
        FR: Reflète `note` en `.md` propre. None si désactivé, exclu, ou
            si l'écriture échoue.
        """
        if not self.enabled:
            return None

        # Match `list_notes` defaults: deleted & archived notes are not
        # exported. If we previously exported one, clean it up.
        if note.is_deleted or note.is_archived:
            self.remove(note.id)
            return None

        try:
            return self._do_export(note)
        except Exception as exc:  # noqa: BLE001 — never break save_note
            logger.warning(f"auto-export failed for note {note.id}: {exc}")
            return None

    def remove(self, note_id: str) -> None:
        """
        EN: Drop the exported file (if any) and the index entry for `note_id`.
            Safe to call when auto-export is disabled or the note was never
            exported.
        FR: Supprime le fichier exporté et l'entrée d'index pour `note_id`.
        """
        if not self.enabled:
            return

        with self._lock:
            index = self._load_index()
            if note_id not in index:
                return
            old_name = index.pop(note_id)
            old_path = self.target_dir / old_name
            try:
                old_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(f"auto-export delete failed ({old_path}): {exc}")
            self._save_index(index)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _do_export(self, note: Note) -> Optional[Path]:
        from nexanote.storage.file_store import _atomic_write

        with self._lock:
            target_dir = self.target_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            index = self._load_index()
            previous_name = index.get(note.id)

            base = sanitize_filename(note.title)
            desired = f"{base}.md"

            # Names taken by other notes (and untracked preexisting files).
            in_use = self._names_in_use(index, exclude_note_id=note.id)

            previous_path = (
                target_dir / previous_name if previous_name else None
            )

            if previous_name == desired and previous_path and previous_path.exists():
                # Title unchanged → keep writing to the same file.
                target_name = previous_name
            elif previous_name and previous_path and previous_path.exists():
                # Title changed → pick a fresh name; old file deleted below.
                target_name = _unique_name(base, in_use)
            else:
                # First export for this note (or previous file vanished).
                target_name = _unique_name(base, in_use)

            target_path = target_dir / target_name

            try:
                _atomic_write(target_path, _note_body(note).encode("utf-8"))
            except OSError as exc:
                logger.warning(
                    f"auto-export write failed for {note.title!r}: {exc}"
                )
                return None

            # If the title moved us to a different filename, drop the old one.
            if (
                previous_name
                and previous_name != target_name
                and previous_path is not None
                and previous_path != target_path
            ):
                try:
                    previous_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning(
                        f"auto-export: removing stale {previous_path}: {exc}"
                    )

            index[note.id] = target_name
            self._save_index(index)
            return target_path

    def _names_in_use(
        self,
        index: dict[str, str],
        exclude_note_id: str,
    ) -> set[str]:
        """Names (lowercased) we must not overwrite when picking a target."""
        target_dir = self.target_dir
        in_use: set[str] = set()

        # Other notes whose mirrored file still exists.
        for other_id, other_name in index.items():
            if other_id == exclude_note_id:
                continue
            if (target_dir / other_name).exists():
                in_use.add(other_name.lower())

        # Files in the directory that aren't tracked at all (user-managed).
        tracked_lower = {n.lower() for n in index.values()}
        try:
            for path in target_dir.glob("*.md"):
                if path.name.lower() not in tracked_lower:
                    in_use.add(path.name.lower())
        except OSError as exc:
            logger.debug(f"auto-export: scanning {target_dir} failed: {exc}")

        return in_use


__all__ = [
    "AutoExportConfig",
    "AutoExporter",
    "ENV_AUTO_EXPORT",
    "ENV_EXPORT_DIR",
    "export_all",
    "export_note",
    "sanitize_filename",
]
