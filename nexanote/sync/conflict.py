"""
NexaNote — Gestion des conflits de synchronisation

Quand deux appareils modifient la même note sans connexion,
on a un conflit. Ce module décide quoi faire.

Stratégies :
  - LAST_WRITE_WINS  : la modification la plus récente gagne (défaut)
  - KEEP_BOTH        : on garde les deux versions (safe, mais verbeux)
  - MERGE_STROKES    : on fusionne les strokes manuscrits (meilleur pour l'encre)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from nexanote.models.note import InkStroke, Note, Page, SyncStatus

logger = logging.getLogger("nexanote.sync.conflict")


class ConflictStrategy(str, Enum):
    LAST_WRITE_WINS = "last_write_wins"
    KEEP_BOTH = "keep_both"
    MERGE_STROKES = "merge_strokes"


@dataclass
class ConflictResult:
    """Résultat d'une résolution de conflit."""
    strategy_used: ConflictStrategy
    winner: Note                          # Note à sauvegarder comme version principale
    conflict_copy: Optional[Note] = None  # Version conflictuelle (si KEEP_BOTH)
    strokes_merged: int = 0               # Nb de strokes fusionnés (si MERGE_STROKES)
    message: str = ""

    def had_conflict(self) -> bool:
        return self.conflict_copy is not None or self.strokes_merged > 0


class ConflictResolver:
    """
    Résout les conflits entre une note locale et une note distante.

    Exemple d'utilisation :
        resolver = ConflictResolver(strategy=ConflictStrategy.MERGE_STROKES)
        result = resolver.resolve(local_note, remote_note)
        db.save_note(result.winner)
    """

    def __init__(self, strategy: ConflictStrategy = ConflictStrategy.MERGE_STROKES) -> None:
        self.strategy = strategy

    def resolve(self, local: Note, remote: Note) -> ConflictResult:
        """
        Compare une note locale et distante, retourne la version résolue.

        Args:
            local:  Version locale (modifiée hors ligne sur cet appareil)
            remote: Version distante (récupérée du serveur WebDAV)
        """
        assert local.id == remote.id, "On ne peut résoudre que deux versions de la même note"

        # Pas de conflit réel — une des deux n'a pas changé
        if local.updated_at == remote.updated_at:
            logger.debug(f"Note {local.id[:8]} : pas de conflit")
            return ConflictResult(
                strategy_used=self.strategy,
                winner=local,
                message="Aucun conflit — versions identiques",
            )

        logger.info(
            f"Conflit détecté sur note {local.id[:8]} "
            f"(local: {local.updated_at.isoformat()}, "
            f"remote: {remote.updated_at.isoformat()})"
        )

        if self.strategy == ConflictStrategy.LAST_WRITE_WINS:
            return self._last_write_wins(local, remote)
        elif self.strategy == ConflictStrategy.KEEP_BOTH:
            return self._keep_both(local, remote)
        elif self.strategy == ConflictStrategy.MERGE_STROKES:
            return self._merge_strokes(local, remote)

        # Fallback
        return self._last_write_wins(local, remote)

    # ------------------------------------------------------------------
    # Stratégie 1 : Last Write Wins
    # ------------------------------------------------------------------

    def _last_write_wins(self, local: Note, remote: Note) -> ConflictResult:
        """La note modifiée en dernier gagne. Simple et prévisible."""
        if local.updated_at >= remote.updated_at:
            winner, loser = local, remote
            msg = "Conflit résolu : version locale gagne (plus récente)"
        else:
            winner, loser = remote, local
            msg = "Conflit résolu : version distante gagne (plus récente)"

        # Forcer SYNCED directement — sans passer par touch()
        winner.sync_status = SyncStatus.SYNCED
        logger.info(msg)
        return ConflictResult(
            strategy_used=ConflictStrategy.LAST_WRITE_WINS,
            winner=winner,
            message=msg,
        )

    # ------------------------------------------------------------------
    # Stratégie 2 : Keep Both
    # ------------------------------------------------------------------

    def _keep_both(self, local: Note, remote: Note) -> ConflictResult:
        """
        Conserve les deux versions.
        La distante devient la principale, la locale est renommée en copie conflictuelle.
        C'est ce que fait Nextcloud avec les fichiers.
        """
        import copy
        from datetime import timezone

        # La version distante devient la principale
        remote.sync_status = SyncStatus.SYNCED

        # La version locale devient une copie de conflit avec un nouvel ID
        import uuid as _uuid
        conflict_copy = copy.deepcopy(local)
        conflict_copy.id = str(_uuid.uuid4())   # Nouvel ID — copie indépendante
        ts = local.updated_at.strftime("%Y-%m-%d_%H-%M")
        conflict_copy.title = f"{local.title} (conflit {ts})"
        conflict_copy.sync_status = SyncStatus.LOCAL_ONLY

        msg = (
            f"Conflit résolu : deux versions conservées. "
            f"Copie locale renommée en '{conflict_copy.title}'"
        )
        logger.info(msg)
        return ConflictResult(
            strategy_used=ConflictStrategy.KEEP_BOTH,
            winner=remote,
            conflict_copy=conflict_copy,
            message=msg,
        )

    # ------------------------------------------------------------------
    # Stratégie 3 : Merge Strokes (meilleure pour les notes manuscrites)
    # ------------------------------------------------------------------

    def _merge_strokes(self, local: Note, remote: Note) -> ConflictResult:
        """
        Fusionne intelligemment les strokes des deux versions.

        Logique :
        - Les métadonnées (titre, tags) : last-write-wins
        - Le contenu texte : last-write-wins par page
        - Les strokes manuscrits : union des deux sets par ID unique
          → Un stroke qui n'existe que sur un appareil est conservé
          → Un stroke présent des deux côtés garde la version locale

        Cette stratégie est idéale pour l'écriture manuscrite car deux
        personnes (ou le même utilisateur sur deux appareils) peuvent
        ajouter des strokes différents sur la même page sans conflit.
        """
        import copy

        # Base : la version la plus récente pour les métadonnées
        if local.updated_at >= remote.updated_at:
            base, other = local, remote
        else:
            base, other = remote, local

        merged = copy.deepcopy(base)
        total_merged = 0

        # Fusionner page par page
        for page in merged.pages:
            other_page = _find_page(other, page.page_number)
            if other_page is None:
                continue

            # Contenu texte : version la plus récente gagne
            if other_page.updated_at > page.updated_at:
                page.typed_content = other_page.typed_content

            # Strokes : union par ID
            local_ids = {s.id for s in page.strokes}
            new_strokes = []
            for stroke in other_page.strokes:
                if stroke.id not in local_ids:
                    new_strokes.append(stroke)
                    total_merged += 1

            if new_strokes:
                page.strokes.extend(new_strokes)
                # Trier par date de création pour un ordre cohérent
                page.strokes.sort(key=lambda s: s.created_at)
                logger.debug(
                    f"Page {page.page_number} : "
                    f"{len(new_strokes)} strokes fusionnés depuis l'autre version"
                )

        merged.sync_status = SyncStatus.SYNCED
        merged.updated_at = datetime.now(timezone.utc)  # Mettre à jour sans passer par touch()

        msg = (
            f"Conflit résolu par fusion : "
            f"{total_merged} strokes ajoutés depuis la version distante"
            if total_merged > 0
            else "Conflit résolu : aucun stroke nouveau à fusionner"
        )
        logger.info(msg)

        return ConflictResult(
            strategy_used=ConflictStrategy.MERGE_STROKES,
            winner=merged,
            strokes_merged=total_merged,
            message=msg,
        )


def _find_page(note: Note, page_number: int) -> Optional[Page]:
    for page in note.pages:
        if page.page_number == page_number:
            return page
    return None
