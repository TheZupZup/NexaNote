"""
NexaNote — Sync state registry / Registre d'état de synchronisation.

EN: Tracks per-data-dir sync metadata that doesn't belong inside individual
    notes:
      * ``adopted`` — remote paths we've already imported and the local
        note id they map to. Lets a re-import of an externally renamed
        legacy file fall back from "match by id" to "match by remote path".
      * ``ignored`` — remote paths we've decided not to import (typically
        legacy / manually-added Markdown files without a NexaNote
        frontmatter id). Required to keep pull idempotent — without this
        registry the engine re-imports those files on every sync, which
        produced the duplicate-creation bug this module fixes.

    The registry lives at ``<data_dir>/.nexanote_sync_state.json``. Failures
    to read or write it never break the sync flow — at worst we lose the
    "already-decided" knowledge for one session.

FR: Registre d'état de synchro par data_dir. Mémorise les chemins distants
    déjà adoptés (avec leur id local) et ceux ignorés (notes manuelles
    sans frontmatter NexaNote). Sans ce registre, le moteur réimportait
    les .md hérités à chaque sync et créait des doublons.

    Stocké dans ``<data_dir>/.nexanote_sync_state.json``. Toute erreur
    de lecture/écriture est non-bloquante.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexanote.sync.state")

SYNC_STATE_FILENAME = ".nexanote_sync_state.json"
SYNC_STATE_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AdoptedEntry:
    """A remote path we've imported into the local store."""

    local_id: str
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict:
        return {
            "local_id": self.local_id,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AdoptedEntry":
        return cls(
            local_id=str(data.get("local_id", "")),
            first_seen=str(data.get("first_seen", "")),
            last_seen=str(data.get("last_seen", "")),
        )


@dataclass
class IgnoredEntry:
    """A remote path we've explicitly decided to skip."""

    reason: str
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IgnoredEntry":
        return cls(
            reason=str(data.get("reason", "")),
            first_seen=str(data.get("first_seen", "")),
            last_seen=str(data.get("last_seen", "")),
        )


@dataclass
class SyncState:
    """
    EN: In-memory view of the on-disk registry. Mutations are thread-safe
        but the JSON file is rewritten only when ``save()`` is called —
        callers typically save once per sync session.
    FR: Vue mémoire du registre. Mutations thread-safe ; on n'écrit le
        fichier que via ``save()`` — appelé à la fin d'une session de sync.
    """

    path: Path
    adopted: dict[str, AdoptedEntry] = field(default_factory=dict)
    ignored: dict[str, IgnoredEntry] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, data_dir: Path) -> "SyncState":
        """
        EN: Load the registry from ``<data_dir>/.nexanote_sync_state.json``.
            Returns an empty state when the file is missing or unreadable —
            sync still works, just without the "previously-decided" memory.
        FR: Charge le registre. Retourne un état vide si le fichier manque
            ou est illisible — la sync fonctionne, simplement sans mémoire.
        """
        path = Path(data_dir) / SYNC_STATE_FILENAME
        state = cls(path=path)
        if not path.exists():
            return state
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "sync state unreadable (%s): %s — starting fresh", path, exc
            )
            return state
        if not isinstance(payload, dict):
            return state

        adopted_raw = payload.get("adopted") or {}
        if isinstance(adopted_raw, dict):
            for key, value in adopted_raw.items():
                if isinstance(key, str) and isinstance(value, dict):
                    state.adopted[key] = AdoptedEntry.from_dict(value)

        ignored_raw = payload.get("ignored") or {}
        if isinstance(ignored_raw, dict):
            for key, value in ignored_raw.items():
                if isinstance(key, str) and isinstance(value, dict):
                    state.ignored[key] = IgnoredEntry.from_dict(value)

        return state

    def save(self) -> None:
        """Persist the registry to disk atomically. Failures are non-fatal."""
        with self._lock:
            payload = {
                "version": SYNC_STATE_VERSION,
                "adopted": {k: v.to_dict() for k, v in self.adopted.items()},
                "ignored": {k: v.to_dict() for k, v in self.ignored.items()},
            }
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("sync state write failed (%s): %s", self.path, exc)

    # ------------------------------------------------------------------
    # Adopted entries
    # ------------------------------------------------------------------

    def get_adopted_local_id(self, remote_path: str) -> Optional[str]:
        """Return the local note id we previously bound to ``remote_path``."""
        with self._lock:
            entry = self.adopted.get(remote_path)
            return entry.local_id if entry else None

    def mark_adopted(self, remote_path: str, local_id: str) -> None:
        """
        EN: Record that ``remote_path`` is bound to ``local_id``. If the
            same path was previously ignored, drop the ignored marker —
            the user (or NexaNote) has clearly decided to take it.
        FR: Mémorise l'adoption. Annule un éventuel marquage "ignoré".
        """
        now = _utc_now_iso()
        with self._lock:
            entry = self.adopted.get(remote_path)
            if entry is None:
                self.adopted[remote_path] = AdoptedEntry(
                    local_id=local_id,
                    first_seen=now,
                    last_seen=now,
                )
            else:
                entry.local_id = local_id
                entry.last_seen = now
            self.ignored.pop(remote_path, None)

    # ------------------------------------------------------------------
    # Ignored entries
    # ------------------------------------------------------------------

    def is_ignored(self, remote_path: str) -> bool:
        with self._lock:
            return remote_path in self.ignored

    def get_ignored_reason(self, remote_path: str) -> Optional[str]:
        with self._lock:
            entry = self.ignored.get(remote_path)
            return entry.reason if entry else None

    def mark_ignored(self, remote_path: str, reason: str) -> None:
        """Record (or refresh) the ignore marker for ``remote_path``."""
        now = _utc_now_iso()
        with self._lock:
            entry = self.ignored.get(remote_path)
            if entry is None:
                self.ignored[remote_path] = IgnoredEntry(
                    reason=reason,
                    first_seen=now,
                    last_seen=now,
                )
            else:
                entry.reason = reason
                entry.last_seen = now

    def touch_ignored(self, remote_path: str) -> None:
        """Bump the ``last_seen`` timestamp without changing the reason."""
        with self._lock:
            entry = self.ignored.get(remote_path)
            if entry is not None:
                entry.last_seen = _utc_now_iso()

    def count_ignored(self) -> int:
        with self._lock:
            return len(self.ignored)

    def all_ignored_paths(self) -> list[str]:
        with self._lock:
            return list(self.ignored.keys())

    def all_adopted_paths(self) -> list[str]:
        with self._lock:
            return list(self.adopted.keys())


__all__ = [
    "AdoptedEntry",
    "IgnoredEntry",
    "SyncState",
    "SYNC_STATE_FILENAME",
    "SYNC_STATE_VERSION",
]
