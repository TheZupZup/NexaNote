"""
WebDAV Sync Client / Client de synchronisation WebDAV

EN: Runs on the device (Linux/Android/Windows). Compares local notes with a
    remote WebDAV server and synchronises them intelligently.
    Sync flow: PULL → DIFF → RESOLVE CONFLICTS → PUSH → COMMIT (mark SYNCED)

FR: Tourne sur l'appareil. Compare les notes locales avec un serveur WebDAV
    distant et les synchronise intelligemment.
    Flux : PULL → DIFF → RÉSOUDRE LES CONFLITS → PUSH → COMMIT (marquer SYNCED)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from urllib.parse import urljoin, quote

import requests
from requests.auth import HTTPBasicAuth

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point, SyncStatus
from nexanote.storage.database import NexaNoteDB
from nexanote.sync.conflict import ConflictResolver, ConflictStrategy

logger = logging.getLogger("nexanote.sync.client")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SyncConfig:
    """Configuration de connexion au serveur WebDAV."""
    server_url: str               # ex: http://192.168.1.10:8765/
    username: str = "nexanote"
    password: str = "nexanote"
    timeout_seconds: int = 15
    conflict_strategy: ConflictStrategy = ConflictStrategy.MERGE_STROKES
    verify_ssl: bool = True


# ---------------------------------------------------------------------------
# Résultat de sync
# ---------------------------------------------------------------------------

class SyncEventType(str, Enum):
    PULL_START = "pull_start"
    PUSH_START = "push_start"
    NOTE_PULLED = "note_pulled"
    NOTE_PUSHED = "note_pushed"
    CONFLICT_RESOLVED = "conflict_resolved"
    ERROR = "error"
    COMPLETE = "complete"


@dataclass
class SyncEvent:
    type: SyncEventType
    message: str
    note_id: Optional[str] = None
    error: Optional[Exception] = None


@dataclass
class SyncReport:
    """Résumé d'une session de synchronisation."""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    notes_pulled: int = 0
    notes_pushed: int = 0
    conflicts_resolved: int = 0
    errors: list[str] = field(default_factory=list)
    events: list[SyncEvent] = field(default_factory=list)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)

    def duration_seconds(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0

    def success(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        return (
            f"Sync terminée en {self.duration_seconds():.1f}s — "
            f"{self.notes_pulled} reçues, {self.notes_pushed} envoyées, "
            f"{self.conflicts_resolved} conflits résolus"
            + (f", {len(self.errors)} erreurs" if self.errors else "")
        )


# ---------------------------------------------------------------------------
# Client WebDAV bas niveau
# ---------------------------------------------------------------------------

class WebDAVClient:
    """
    Client HTTP bas niveau pour parler au serveur WebDAV NexaNote.
    Gère les requêtes GET/PUT/PROPFIND/MKCOL.
    """

    def __init__(self, config: SyncConfig) -> None:
        self.config = config
        self.base_url = config.server_url.rstrip("/") + "/"
        self.auth = HTTPBasicAuth(config.username, config.password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.verify = config.verify_ssl

    def _url(self, *parts: str) -> str:
        """Construit une URL à partir de parties encodées."""
        path = "/".join(quote(p, safe="") for p in parts if p)
        return urljoin(self.base_url, path)

    def ping(self) -> bool:
        """Vérifie que le serveur est accessible."""
        try:
            resp = self.session.request(
                "OPTIONS",
                self.base_url,
                timeout=self.config.timeout_seconds,
            )
            return resp.status_code < 400
        except requests.RequestException:
            return False

    def list_notebooks(self) -> list[dict]:
        """PROPFIND sur / — retourne la liste des carnets."""
        return self._propfind(self.base_url, depth=1)

    def list_notes(self, notebook_slug: str) -> list[dict]:
        """PROPFIND sur /{notebook} — retourne la liste des notes."""
        return self._propfind(self._url(notebook_slug), depth=1)

    def list_note_files(self, notebook_slug: str, note_slug: str) -> list[dict]:
        """PROPFIND sur /{notebook}/{note} — retourne les fichiers."""
        return self._propfind(self._url(notebook_slug, note_slug), depth=1)

    def get_note_meta(self, notebook_slug: str, note_slug: str) -> Optional[dict]:
        """GET /{notebook}/{note}/note.json"""
        url = self._url(notebook_slug, note_slug, "note.json")
        try:
            resp = self.session.get(url, timeout=self.config.timeout_seconds)
            if resp.status_code == 200:
                return resp.json()
            return None
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"GET note.json échoué ({url}): {e}")
            return None

    def get_ink_page(self, notebook_slug: str, note_slug: str, page_num: int) -> Optional[dict]:
        """GET /{notebook}/{note}/page_N.ink"""
        url = self._url(notebook_slug, note_slug, f"page_{page_num}.ink")
        try:
            resp = self.session.get(url, timeout=self.config.timeout_seconds)
            if resp.status_code == 200:
                return resp.json()
            return None
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"GET page.ink échoué ({url}): {e}")
            return None

    def put_note_meta(
        self, notebook_slug: str, note_slug: str, data: dict
    ) -> bool:
        """PUT /{notebook}/{note}/note.json"""
        url = self._url(notebook_slug, note_slug, "note.json")
        try:
            resp = self.session.put(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=self.config.timeout_seconds,
            )
            return resp.status_code in (200, 201, 204)
        except requests.RequestException as e:
            logger.error(f"PUT note.json échoué ({url}): {e}")
            return False

    def put_ink_page(
        self, notebook_slug: str, note_slug: str, page_num: int, data: dict
    ) -> bool:
        """PUT /{notebook}/{note}/page_N.ink"""
        url = self._url(notebook_slug, note_slug, f"page_{page_num}.ink")
        try:
            resp = self.session.put(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=self.config.timeout_seconds,
            )
            return resp.status_code in (200, 201, 204)
        except requests.RequestException as e:
            logger.error(f"PUT page.ink échoué ({url}): {e}")
            return False

    def create_notebook_dir(self, notebook_slug: str) -> bool:
        """MKCOL /{notebook} — crée un dossier carnet sur le serveur."""
        url = self._url(notebook_slug)
        try:
            resp = self.session.request(
                "MKCOL", url, timeout=self.config.timeout_seconds
            )
            # 200/201 = créé, 405 = la ressource existe déjà (WebDAV standard)
            return resp.status_code in (200, 201, 405)
        except requests.RequestException as e:
            logger.error(f"MKCOL échoué ({url}): {e}")
            return False

    def create_note_dir(self, notebook_slug: str, note_slug: str) -> bool:
        """MKCOL /{notebook}/{note}"""
        url = self._url(notebook_slug, note_slug)
        try:
            resp = self.session.request(
                "MKCOL", url, timeout=self.config.timeout_seconds
            )
            # 200/201 = créé, 405 = la ressource existe déjà (WebDAV standard)
            return resp.status_code in (200, 201, 405)
        except requests.RequestException as e:
            logger.error(f"MKCOL note échoué ({url}): {e}")
            return False

    def _propfind(self, url: str, depth: int = 1) -> list[dict]:
        """
        PROPFIND WebDAV — liste les ressources à un niveau donné.
        Retourne une liste simplifiée de {name, href, is_collection, last_modified}.
        """
        try:
            resp = self.session.request(
                "PROPFIND",
                url,
                headers={
                    "Depth": str(depth),
                    "Content-Type": "application/xml",
                },
                data=b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:getlastmodified/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>""",
                timeout=self.config.timeout_seconds,
            )

            if resp.status_code == 207:  # Multi-Status
                return self._parse_propfind(resp.text, url)
            return []
        except requests.RequestException as e:
            logger.error(f"PROPFIND échoué ({url}): {e}")
            return []

    def _parse_propfind(self, xml_text: str, base_url: str) -> list[dict]:
        """Parse la réponse XML PROPFIND en liste de ressources."""
        import xml.etree.ElementTree as ET

        resources = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"D": "DAV:"}

            for response in root.findall("D:response", ns):
                href = response.findtext("D:href", "", ns)
                if not href or href.rstrip("/") == base_url.rstrip("/"):
                    continue  # On ignore la ressource elle-même

                display_name = response.findtext(
                    ".//D:displayname", "", ns
                ) or href.rstrip("/").split("/")[-1]

                last_mod_text = response.findtext(".//D:getlastmodified", "", ns)
                last_modified = None
                if last_mod_text:
                    try:
                        from email.utils import parsedate_to_datetime
                        last_modified = parsedate_to_datetime(last_mod_text)
                    except Exception:
                        pass

                is_col = response.find(".//D:collection", ns) is not None

                resources.append({
                    "name": href.rstrip("/").split("/")[-1],
                    "href": href,
                    "display_name": display_name,
                    "is_collection": is_col,
                    "last_modified": last_modified,
                })
        except ET.ParseError as e:
            logger.error(f"Erreur parsing PROPFIND XML: {e}")

        return resources


# ---------------------------------------------------------------------------
# Moteur de synchronisation
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    import re
    name = name.strip().lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "-", name)
    return name or "sans-titre"


def _note_to_slug(note: Note) -> str:
    return _slugify(note.title) + "__" + note.id[:8]


def _notebook_to_slug(nb: Notebook) -> str:
    return _slugify(nb.name) + "__" + nb.id[:8]


def _deserialize_note(meta: dict, ink_pages: dict[int, dict]) -> Note:
    """Reconstruit une Note à partir des données JSON du serveur."""
    note = Note(
        id=meta["id"],
        title=meta.get("title", "Sans titre"),
        note_type=NoteType(meta.get("type", "typed")),
        tags=meta.get("tags", []),
        is_pinned=meta.get("is_pinned", False),
    )
    if meta.get("created_at"):
        note.created_at = datetime.fromisoformat(meta["created_at"])
    if meta.get("updated_at"):
        note.updated_at = datetime.fromisoformat(meta["updated_at"])

    for page_data in meta.get("pages", []):
        num = page_data["page_number"]
        page = Page(
            note_id=note.id,
            page_number=num,
            template=page_data.get("template", "blank"),
            typed_content=page_data.get("typed_content", ""),
        )
        # Charger les strokes si disponibles
        if num in ink_pages:
            for s_data in ink_pages[num].get("strokes", []):
                points = [
                    Point(
                        x=p["x"], y=p["y"],
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
                page.strokes.append(stroke)
        note.pages.append(page)

    note.sync_status = SyncStatus.SYNCED
    return note


def _serialize_note_meta(note: Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "type": note.note_type.value,
        "tags": note.tags,
        "is_pinned": note.is_pinned,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
        "pages": [
            {
                "page_number": p.page_number,
                "template": p.template,
                "typed_content": p.typed_content,
            }
            for p in note.pages
        ],
    }


def _serialize_ink_page(page: Page) -> dict:
    return {
        "page_id": page.id,
        "note_id": page.note_id,
        "page_number": page.page_number,
        "template": page.template,
        "width_px": page.width_px,
        "height_px": page.height_px,
        "updated_at": page.updated_at.isoformat(),
        "strokes": [
            {
                "id": s.id,
                "color": s.color,
                "width": s.width,
                "tool": s.tool,
                "created_at": s.created_at.isoformat(),
                "points": [
                    {"x": p.x, "y": p.y, "pressure": p.pressure, "ts": p.timestamp_ms}
                    for p in s.points
                ],
            }
            for s in page.strokes
        ],
    }


class NexaNoteSyncEngine:
    """
    Moteur de synchronisation principal.
    Orchestre pull → diff → resolve → push.
    """

    def __init__(self, db: NexaNoteDB, config: SyncConfig) -> None:
        self.db = db
        self.config = config
        self.client = WebDAVClient(config)
        self.resolver = ConflictResolver(config.conflict_strategy)

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def sync(self) -> SyncReport:
        """
        Lance une session de synchronisation complète.
        Retourne un rapport détaillé.
        """
        report = SyncReport()
        logger.info("Début de la synchronisation NexaNote")

        # Vérifier la connexion
        if not self.client.ping():
            msg = f"Impossible de joindre le serveur : {self.config.server_url}"
            logger.error(msg)
            report.errors.append(msg)
            report.finish()
            return report

        try:
            # 1. Pull depuis le serveur
            self._pull(report)
            # 2. Push vers le serveur
            self._push(report)
        except Exception as e:
            logger.exception("Erreur inattendue pendant la sync")
            report.errors.append(str(e))

        report.finish()
        logger.info(report.summary())
        return report

    # ------------------------------------------------------------------
    # PULL — récupérer les changements du serveur
    # ------------------------------------------------------------------

    def _pull(self, report: SyncReport) -> None:
        """
        Parcourt les carnets et notes du serveur.
        Pour chaque note distante :
          - Si inconnue localement → importer
          - Si connue et identique → skip
          - Si connue et différente → résoudre le conflit
        """
        report.events.append(SyncEvent(SyncEventType.PULL_START, "Pull depuis le serveur"))
        logger.info("PULL — récupération des notes distantes")

        remote_notebooks = self.client.list_notebooks()
        logger.debug(f"  {len(remote_notebooks)} carnets trouvés sur le serveur")

        for nb_entry in remote_notebooks:
            if not nb_entry["is_collection"]:
                continue
            nb_slug = nb_entry["name"]
            self._pull_notebook(nb_slug, report)

    def _pull_notebook(self, nb_slug: str, report: SyncReport) -> None:
        remote_notes = self.client.list_notes(nb_slug)
        logger.debug(f"  Carnet {nb_slug} : {len(remote_notes)} notes")

        for note_entry in remote_notes:
            if not note_entry["is_collection"]:
                continue
            note_slug = note_entry["name"]
            try:
                self._pull_note(nb_slug, note_slug, report)
            except Exception as e:
                msg = f"Erreur pull note {note_slug}: {e}"
                logger.error(msg)
                report.errors.append(msg)

    def _pull_note(self, nb_slug: str, note_slug: str, report: SyncReport) -> None:
        """Pull une note spécifique depuis le serveur."""
        meta = self.client.get_note_meta(nb_slug, note_slug)
        if not meta:
            return

        note_id = meta.get("id")
        if not note_id:
            return

        # Charger les pages manuscrites
        ink_pages: dict[int, dict] = {}
        for page_data in meta.get("pages", []):
            num = page_data["page_number"]
            ink = self.client.get_ink_page(nb_slug, note_slug, num)
            if ink:
                ink_pages[num] = ink

        remote_note = _deserialize_note(meta, ink_pages)

        # Chercher la version locale
        local_note = self.db.get_note(note_id, load_pages=True)

        if local_note is None:
            # Nouvelle note inconnue localement — import direct
            self.db.save_note(remote_note)
            report.notes_pulled += 1
            report.events.append(SyncEvent(
                SyncEventType.NOTE_PULLED,
                f"Nouvelle note importée : {remote_note.title}",
                note_id=note_id,
            ))
            logger.info(f"  ← Importée : {remote_note.title}")

        elif local_note.sync_status == SyncStatus.MODIFIED:
            # Conflit potentiel — résoudre
            result = self.resolver.resolve(local_note, remote_note)
            self.db.save_note(result.winner)

            if result.conflict_copy:
                self.db.save_note(result.conflict_copy)

            report.notes_pulled += 1
            report.conflicts_resolved += 1
            report.events.append(SyncEvent(
                SyncEventType.CONFLICT_RESOLVED,
                result.message,
                note_id=note_id,
            ))
            logger.info(f"  ⚡ Conflit résolu : {local_note.title} — {result.message}")

        else:
            # Pas de modification locale — appliquer la version distante si plus récente
            if remote_note.updated_at > local_note.updated_at:
                self.db.save_note(remote_note)
                report.notes_pulled += 1
                logger.info(f"  ↓ Mise à jour : {remote_note.title}")

    # ------------------------------------------------------------------
    # PUSH — envoyer les notes modifiées localement
    # ------------------------------------------------------------------

    def _push(self, report: SyncReport) -> None:
        """
        Envoie toutes les notes marquées MODIFIED ou LOCAL_ONLY
        vers le serveur WebDAV.
        """
        report.events.append(SyncEvent(SyncEventType.PUSH_START, "Push vers le serveur"))
        logger.info("PUSH — envoi des notes locales modifiées")

        # Récupérer toutes les notes à pousser
        all_notes = self.db.list_notes(include_deleted=False, include_archived=True)
        to_push = [
            n for n in all_notes
            if n.sync_status in (SyncStatus.MODIFIED, SyncStatus.LOCAL_ONLY)
        ]
        logger.debug(f"  {len(to_push)} notes à pousser")

        for note in to_push:
            try:
                full_note = self.db.get_note(note.id, load_pages=True)
                if full_note:
                    self._push_note(full_note, report)
            except Exception as e:
                msg = f"Erreur push note {note.id[:8]}: {e}"
                logger.error(msg)
                report.errors.append(msg)

    def _push_note(self, note: Note, report: SyncReport) -> None:
        """Pousse une note vers le serveur."""
        # Trouver le carnet pour construire le chemin
        notebook = None
        if note.notebook_id:
            notebook = self.db.get_notebook(note.notebook_id)

        if notebook:
            nb_slug = _notebook_to_slug(notebook)
        else:
            # Notes sans carnet → dossier "sans-carnet" sur le serveur
            nb_slug = "sans-carnet"
            logger.debug(f"Note {note.title!r} sans carnet → dossier '{nb_slug}'")

        note_slug = _note_to_slug(note)

        # Créer le dossier carnet sur le serveur si nécessaire
        nb_entries = self.client.list_notebooks()
        nb_names = {e["name"] for e in nb_entries}
        if nb_slug not in nb_names:
            self.client.create_notebook_dir(nb_slug)

        # Créer le dossier note sur le serveur si nécessaire
        note_entries = self.client.list_notes(nb_slug)
        note_names = {e["name"] for e in note_entries}
        if note_slug not in note_names:
            self.client.create_note_dir(nb_slug, note_slug)

        # PUT note.json
        meta_ok = self.client.put_note_meta(nb_slug, note_slug, _serialize_note_meta(note))

        # PUT page_N.ink pour chaque page
        pages_ok = True
        for page in note.pages:
            ok = self.client.put_ink_page(
                nb_slug, note_slug, page.page_number,
                _serialize_ink_page(page)
            )
            if not ok:
                pages_ok = False

        if meta_ok and pages_ok:
            # Marquer comme SYNCED
            note.sync_status = SyncStatus.SYNCED
            self.db.save_note(note, save_pages=False)
            report.notes_pushed += 1
            report.events.append(SyncEvent(
                SyncEventType.NOTE_PUSHED,
                f"Note envoyée : {note.title}",
                note_id=note.id,
            ))
            logger.info(f"  → Envoyée : {note.title}")
        else:
            msg = f"Échec partiel push : {note.title}"
            report.errors.append(msg)
            logger.warning(msg)
