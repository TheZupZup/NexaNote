"""
NexaNote — Sync planning / Plan de synchronisation.

EN: A ``SyncPlan`` is the *intent* of a sync session: what the engine
    would push, pull, ignore, treat as a conflict, or warn about. It is
    populated as the engine makes decisions and is the single source the
    dry-run mode and the sanitized sync log read from.

    The plan deliberately carries **no note body content** — only stable,
    non-sensitive metadata (note ids, titles, remote paths, short reasons).
    That invariant is what lets us serialise the plan straight into a
    diagnostic log without leaking note text or secrets.

FR: Un ``SyncPlan`` représente l'intention d'une session de sync : ce qui
    serait poussé, tiré, ignoré, considéré comme conflit ou signalé. Il est
    rempli au fil des décisions du moteur et sert de source unique au mode
    dry-run et au journal de sync.

    Le plan ne contient **aucun contenu de note** — uniquement des
    métadonnées non sensibles (ids, titres, chemins distants, motifs).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlannedNote:
    """A note slated to be pushed or pulled. Title is metadata, never body."""

    note_id: str
    title: str

    def to_dict(self) -> dict:
        return {"id": self.note_id, "title": self.title}


@dataclass
class PlannedIgnore:
    """A remote path the engine will skip, with a short reason."""

    remote_path: str
    reason: str

    def to_dict(self) -> dict:
        return {"remote_path": self.remote_path, "reason": self.reason}


@dataclass
class PlannedConflict:
    """
    EN: A note where the local copy has unsynced edits *and* the remote
        copy differs — both sides changed. ``preserved_both`` records that
        we kept a copy of the local version instead of overwriting it.
    FR: Une note modifiée localement dont la version distante diffère —
        les deux côtés ont changé. ``preserved_both`` indique qu'une copie
        locale a été conservée plutôt qu'écrasée.
    """

    note_id: str
    title: str
    resolution: str
    preserved_both: bool

    def to_dict(self) -> dict:
        return {
            "id": self.note_id,
            "title": self.title,
            "resolution": self.resolution,
            "preserved_both": self.preserved_both,
        }


@dataclass
class SyncPlan:
    """
    EN: The intent of a single sync session. Built before (and as) changes
        are applied; in dry-run mode it is built without any writes at all.
    FR: L'intention d'une session de sync. Construit avant (et pendant)
        l'application des changements ; en dry-run, sans aucune écriture.
    """

    notes_to_push: list[PlannedNote] = field(default_factory=list)
    notes_to_pull: list[PlannedNote] = field(default_factory=list)
    notes_to_ignore: list[PlannedIgnore] = field(default_factory=list)
    conflicts: list[PlannedConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def add_push(self, note_id: str, title: str) -> None:
        self.notes_to_push.append(PlannedNote(note_id=note_id, title=title))

    def add_pull(self, note_id: str, title: str) -> None:
        self.notes_to_pull.append(PlannedNote(note_id=note_id, title=title))

    def add_ignore(self, remote_path: str, reason: str) -> None:
        self.notes_to_ignore.append(
            PlannedIgnore(remote_path=remote_path, reason=reason)
        )

    def add_conflict(
        self,
        note_id: str,
        title: str,
        resolution: str,
        preserved_both: bool,
    ) -> None:
        self.conflicts.append(
            PlannedConflict(
                note_id=note_id,
                title=title,
                resolution=resolution,
                preserved_both=preserved_both,
            )
        )

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def counts(self) -> dict:
        return {
            "to_push": len(self.notes_to_push),
            "to_pull": len(self.notes_to_pull),
            "to_ignore": len(self.notes_to_ignore),
            "conflicts": len(self.conflicts),
            "warnings": len(self.warnings),
        }

    def is_empty(self) -> bool:
        return not (
            self.notes_to_push
            or self.notes_to_pull
            or self.notes_to_ignore
            or self.conflicts
            or self.warnings
        )

    def to_dict(self) -> dict:
        return {
            "notes_to_push": [n.to_dict() for n in self.notes_to_push],
            "notes_to_pull": [n.to_dict() for n in self.notes_to_pull],
            "notes_to_ignore": [i.to_dict() for i in self.notes_to_ignore],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "warnings": list(self.warnings),
        }


__all__ = [
    "PlannedNote",
    "PlannedIgnore",
    "PlannedConflict",
    "SyncPlan",
]
