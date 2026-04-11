"""
NexaNote — Modèles de données principaux
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NoteType(str, Enum):
    """Type de contenu d'une note."""
    TYPED = "typed"          # Note textuelle
    HANDWRITTEN = "handwritten"  # Note manuscrite (strokes)
    MIXED = "mixed"          # Les deux


class SyncStatus(str, Enum):
    """État de synchronisation d'un objet."""
    LOCAL_ONLY = "local_only"    # Jamais synchronisé
    SYNCED = "synced"            # À jour
    MODIFIED = "modified"        # Modifié localement, sync en attente
    CONFLICT = "conflict"        # Conflit détecté


# ---------------------------------------------------------------------------
# Stroke (encre manuscrite)
# ---------------------------------------------------------------------------

@dataclass
class Point:
    """Un point d'un stroke avec coordonnées et pression."""
    x: float
    y: float
    pressure: float = 0.5    # 0.0 → 1.0
    timestamp_ms: int = 0    # Temps relatif au début du stroke (ms)


@dataclass
class InkStroke:
    """
    Un trait d'encre = séquence de points capturés lors d'un geste stylet.
    C'est l'unité atomique du dessin manuscrit.
    """
    id: str = field(default_factory=_new_id)
    points: list[Point] = field(default_factory=list)
    color: str = "#000000"       # Couleur hexadécimale
    width: float = 2.0           # Épaisseur de base en pixels
    tool: str = "pen"            # pen | highlighter | eraser
    created_at: datetime = field(default_factory=_now)

    def is_empty(self) -> bool:
        return len(self.points) < 2

    def bounding_box(self) -> tuple[float, float, float, float]:
        """Retourne (x_min, y_min, x_max, y_max)."""
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@dataclass
class Page:
    """
    Une page à l'intérieur d'une note.
    Une note peut avoir plusieurs pages (comme un carnet).
    """
    id: str = field(default_factory=_new_id)
    note_id: str = ""
    page_number: int = 1
    template: str = "blank"          # blank | lined | grid | dotted
    width_px: float = 1404.0         # Taille par défaut = tablette A4
    height_px: float = 1872.0
    strokes: list[InkStroke] = field(default_factory=list)
    typed_content: str = ""          # Contenu texte/markdown si note typée
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def touch(self) -> None:
        """Mettre à jour updated_at après modification."""
        self.updated_at = _now()

    def stroke_count(self) -> int:
        return len(self.strokes)

    def add_stroke(self, stroke: InkStroke) -> None:
        self.strokes.append(stroke)
        self.touch()

    def remove_stroke(self, stroke_id: str) -> bool:
        before = len(self.strokes)
        self.strokes = [s for s in self.strokes if s.id != stroke_id]
        if len(self.strokes) < before:
            self.touch()
            return True
        return False


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------

@dataclass
class Note:
    """
    Une note = conteneur de pages avec métadonnées.
    Peut être typée, manuscrite, ou mixte.
    """
    id: str = field(default_factory=_new_id)
    notebook_id: Optional[str] = None
    title: str = "Sans titre"
    note_type: NoteType = NoteType.TYPED
    tags: list[str] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    is_pinned: bool = False
    is_archived: bool = False
    is_deleted: bool = False
    sync_status: SyncStatus = SyncStatus.LOCAL_ONLY
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()
        if self.sync_status == SyncStatus.SYNCED:
            self.sync_status = SyncStatus.MODIFIED

    def add_page(self, template: str = "blank") -> Page:
        page_number = len(self.pages) + 1
        page = Page(
            note_id=self.id,
            page_number=page_number,
            template=template,
        )
        self.pages.append(page)
        self.touch()
        return page

    def get_page(self, page_number: int) -> Optional[Page]:
        for page in self.pages:
            if page.page_number == page_number:
                return page
        return None

    def page_count(self) -> int:
        return len(self.pages)

    def add_tag(self, tag: str) -> None:
        tag = tag.strip().lower()
        if tag and tag not in self.tags:
            self.tags.append(tag)
            self.touch()

    def remove_tag(self, tag: str) -> None:
        tag = tag.strip().lower()
        if tag in self.tags:
            self.tags.remove(tag)
            self.touch()

    def soft_delete(self) -> None:
        """Suppression logique (corbeille)."""
        self.is_deleted = True
        self.touch()

    def restore(self) -> None:
        self.is_deleted = False
        self.touch()


# ---------------------------------------------------------------------------
# Notebook (Carnet)
# ---------------------------------------------------------------------------

@dataclass
class Notebook:
    """
    Un carnet = collection de notes organisées ensemble.
    Peut contenir des sous-carnets (nested folders).
    """
    id: str = field(default_factory=_new_id)
    parent_id: Optional[str] = None   # Pour les sous-carnets
    name: str = "Nouveau carnet"
    description: str = ""
    color: str = "#6366f1"            # Couleur d'affichage dans l'UI
    icon: str = "notebook"
    is_archived: bool = False
    sync_status: SyncStatus = SyncStatus.LOCAL_ONLY
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()
        if self.sync_status == SyncStatus.SYNCED:
            self.sync_status = SyncStatus.MODIFIED
