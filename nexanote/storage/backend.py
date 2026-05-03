"""
NexaNote — Storage backend factory / Sélecteur de backend de stockage.

EN: Two backends ship side-by-side:
      * ``FileNoteStore``           (default) — YAML frontmatter inside the
                                                Markdown file itself, one
                                                file per note keyed by id.
      * ``PlainMarkdownNoteStore``           — Pure Markdown body in
                                                ``<Title>.md`` plus a
                                                ``<Title>.json`` sidecar for
                                                metadata. Direct Obsidian
                                                vault drop-in.

    The active backend for a given data directory is recorded in a small
    ``.nexanote_storage_mode`` marker file. ``create_store`` reads the
    marker, falls back to the ``NEXANOTE_STORAGE_MODE`` env var, and
    finally to ``yaml`` when nothing is set. This keeps existing data
    directories on the YAML backend unless the user explicitly switches.

FR: Deux backends cohabitent — frontmatter YAML ou Markdown propre +
    sidecar JSON. Le mode actif est enregistré dans un marqueur, avec
    repli sur la variable d'env ``NEXANOTE_STORAGE_MODE`` puis ``yaml``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from nexanote.storage.file_store import FileNoteStore
from nexanote.storage.plain_store import PlainMarkdownNoteStore

if TYPE_CHECKING:
    from nexanote.storage.export import AutoExportConfig

logger = logging.getLogger("nexanote.storage.backend")

# EN: Marker filename inside the data directory. Recording the mode on
#     disk avoids accidental backend swaps when the env var is missing.
# FR: Marqueur dans le data dir — évite un changement de backend accidentel.
MODE_MARKER = ".nexanote_storage_mode"

ENV_STORAGE_MODE = "NEXANOTE_STORAGE_MODE"

MODE_YAML = "yaml"
MODE_PLAIN = "plain"
DEFAULT_MODE = MODE_YAML

NoteStore = Union[FileNoteStore, PlainMarkdownNoteStore]


@dataclass(frozen=True)
class BackendInfo:
    """Resolved backend selection for a data directory."""

    mode: str
    source: str  # "marker", "env", or "default"


def detect_mode(
    data_dir: Path,
    env: Optional[dict] = None,
) -> BackendInfo:
    """
    EN: Resolve which backend to open for `data_dir`. Marker file wins;
        env var is the next choice; otherwise the default (`yaml`) is used.
    FR: Détermine le backend à utiliser. Marqueur > env > défaut (`yaml`).
    """
    data_dir = Path(data_dir)
    marker = data_dir / MODE_MARKER
    if marker.exists():
        try:
            mode = marker.read_text(encoding="utf-8").strip().lower()
        except OSError as exc:
            logger.warning(f"could not read storage mode marker {marker}: {exc}")
            mode = ""
        if mode in (MODE_YAML, MODE_PLAIN):
            return BackendInfo(mode=mode, source="marker")
        logger.warning(
            f"storage mode marker contains unknown value {mode!r} — falling back"
        )

    source_env = env if env is not None else os.environ
    env_mode = (source_env.get(ENV_STORAGE_MODE) or "").strip().lower()
    if env_mode in (MODE_YAML, MODE_PLAIN):
        return BackendInfo(mode=env_mode, source="env")

    return BackendInfo(mode=DEFAULT_MODE, source="default")


def write_mode_marker(data_dir: Path, mode: str) -> None:
    """Pin `data_dir` to `mode` so future opens don't drift."""
    if mode not in (MODE_YAML, MODE_PLAIN):
        raise ValueError(f"unknown storage mode: {mode!r}")
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / MODE_MARKER
    marker.write_text(mode + "\n", encoding="utf-8")


def create_store(
    data_dir: Path,
    mode: Optional[str] = None,
    auto_export: Optional["AutoExportConfig"] = None,
) -> NoteStore:
    """
    EN: Open the right backend for `data_dir`. Pass `mode` to force a
        choice; otherwise `detect_mode` is used. The chosen mode is
        persisted via a marker on first use so subsequent opens stay
        consistent even if the env var disappears.
    FR: Ouvre le bon backend pour `data_dir`. Persiste le choix dans un
        marqueur pour rester stable d'un démarrage à l'autre.
    """
    data_dir = Path(data_dir)
    if mode is None:
        info = detect_mode(data_dir)
        mode = info.mode
        logger.info(
            f"Storage backend: {mode} (source={info.source}, dir={data_dir})"
        )
    else:
        logger.info(f"Storage backend: {mode} (forced, dir={data_dir})")

    # Record the choice so a later run without env vars stays consistent.
    marker = data_dir / MODE_MARKER
    if not marker.exists():
        try:
            write_mode_marker(data_dir, mode)
        except OSError as exc:
            logger.warning(f"could not write storage mode marker: {exc}")

    if mode == MODE_PLAIN:
        return PlainMarkdownNoteStore(data_dir, auto_export=auto_export)
    return FileNoteStore(data_dir, auto_export=auto_export)


__all__ = [
    "BackendInfo",
    "DEFAULT_MODE",
    "ENV_STORAGE_MODE",
    "MODE_MARKER",
    "MODE_PLAIN",
    "MODE_YAML",
    "NoteStore",
    "create_store",
    "detect_mode",
    "write_mode_marker",
]
