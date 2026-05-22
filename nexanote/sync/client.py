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

import copy
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, quote, unquote

import requests
from requests.auth import HTTPBasicAuth

from nexanote.models.note import InkStroke, Note, Notebook, NoteType, Page, Point, SyncStatus
from nexanote.storage.file_store import FileNoteStore, PLAIN_MD_ID_PREFIX
from nexanote.sync.conflict import ConflictResolver, ConflictStrategy
from nexanote.sync.plan import SyncPlan
from nexanote.sync.sync_log import write_sync_log
from nexanote.sync.sync_state import SyncState

logger = logging.getLogger("nexanote.sync.client")

# EN: Fallback folder name on the remote server for notes not assigned to any notebook.
# FR: Nom du dossier de repli sur le serveur distant pour les notes sans carnet.
DEFAULT_NOTEBOOK_SLUG = "uncategorized"


def _is_legacy_remote_id(note_id: Optional[str]) -> bool:
    """
    EN: Return True when ``note_id`` looks like it was synthesised by the
        WebDAV provider for a plain Markdown file with no NexaNote
        frontmatter. Such ids start with ``md.`` (the
        ``PLAIN_MD_ID_PREFIX``) and are not stable across renames — we
        treat them as "legacy / manual" and avoid duplicating them on
        every pull.
    FR: Vrai si ``note_id`` ressemble à un id synthétisé pour un .md
        sans frontmatter NexaNote (préfixe ``md.``). On considère ces
        notes comme "héritées / manuelles".
    """
    if not note_id:
        return True
    return note_id.startswith(PLAIN_MD_ID_PREFIX)


def _remote_path(nb_slug: str, note_slug: str) -> str:
    """Stable string key combining notebook + note slug for the registry."""
    return f"{nb_slug}/{note_slug}"


def _sanitize_request_error(exc: BaseException) -> str:
    """
    EN: Render a requests/network exception into a short, user-safe reason.
        Avoids leaking the full URL (which embeds the server host) and never
        includes auth headers since those are not part of the exception.
    FR: Convertit une exception réseau en motif court et sûr à afficher.
    """
    name = type(exc).__name__
    # Use the exception class name only — the str() of requests exceptions
    # often embeds the full URL, which we don't want to surface to the UI.
    return f"WebDAV upload failed: {name}"


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
    # EN: Remote .md files we deliberately skipped because they have no
    #     NexaNote frontmatter id and aren't safely mappable. Surfaces in
    #     the diagnostic summary so users can tell whether their remote
    #     folder still carries legacy hand-edited files.
    # FR: .md distants délibérément ignorés (sans id NexaNote sûrement
    #     mappable). Exposé dans le résumé pour diagnostiquer.
    notes_ignored_legacy: int = 0
    errors: list[str] = field(default_factory=list)
    events: list[SyncEvent] = field(default_factory=list)
    # EN: The plan backing this session — what was (or would be) pushed,
    #     pulled, ignored, or flagged as a conflict. Populated by the engine
    #     and read by the diagnostic sync log. May be None when a report is
    #     built standalone (e.g. a direct ``_push`` call in tests).
    # FR: Le plan de cette session (poussé/tiré/ignoré/conflits). Rempli par
    #     le moteur, lu par le journal de diagnostic. Peut être None.
    plan: Optional[SyncPlan] = None
    # EN: True when this report describes a dry-run (no files/state written).
    # FR: Vrai quand le rapport décrit un dry-run (aucune écriture).
    dry_run: bool = False

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
            + (
                f", {self.notes_ignored_legacy} héritées ignorées"
                if self.notes_ignored_legacy
                else ""
            )
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

    @staticmethod
    def _is_mkcol_success(status_code: int) -> bool:
        """
        EN: Returns True when a MKCOL response means the collection was created
            or already exists. Per the WebDAV spec (RFC 4918):
              201 = Created
              405 = Method Not Allowed → the resource already exists (success for us)
        FR: Retourne True si la réponse MKCOL signifie que la collection a été
            créée ou existe déjà. Selon la spec WebDAV (RFC 4918) :
              201 = Créé
              405 = Method Not Allowed → la ressource existe déjà (succès pour nous)
        """
        return status_code in (200, 201, 405)

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
    ) -> tuple[bool, Optional[str]]:
        """
        EN: PUT /{notebook}/{note}/note.json. On 409 (parent missing) we
            transparently MKCOL the parents and retry once — keeps push
            reliable when the server hasn't seen this notebook/note yet.
        FR: PUT note.json. Si le serveur renvoie 409 (parent absent), on
            crée les dossiers parents en MKCOL et on retente une fois.
        Returns (ok, reason_if_failed).
        """
        url = self._url(notebook_slug, note_slug, "note.json")
        return self._put_json(
            url,
            data,
            label="note.json",
            mkcol_paths=(notebook_slug, f"{notebook_slug}/{note_slug}"),
        )

    def put_ink_page(
        self, notebook_slug: str, note_slug: str, page_num: int, data: dict
    ) -> tuple[bool, Optional[str]]:
        """
        EN: PUT /{notebook}/{note}/page_N.ink with the same MKCOL-on-409
            recovery as ``put_note_meta``.
        FR: PUT page_N.ink avec la même récupération MKCOL sur 409.
        """
        url = self._url(notebook_slug, note_slug, f"page_{page_num}.ink")
        return self._put_json(
            url,
            data,
            label=f"page_{page_num}.ink",
            mkcol_paths=(notebook_slug, f"{notebook_slug}/{note_slug}"),
            page_num=page_num,
        )

    def _put_json(
        self,
        url: str,
        data: dict,
        label: str,
        mkcol_paths: tuple[str, ...],
        page_num: Optional[int] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        EN: Internal helper that performs PUT with a single MKCOL-and-retry
            cycle when the server returns 409 (parent collection missing).
            Treats every non-2xx response uniformly so callers get a stable
            (ok, reason) shape.
        FR: Helper interne pour PUT avec récupération MKCOL+retry sur 409.
        """
        prefix = f"{label}: " if page_num is None else f"page {page_num}: "

        def _do_put() -> requests.Response:
            return self.session.put(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=self.config.timeout_seconds,
            )

        try:
            resp = _do_put()
            if resp.status_code in (200, 201, 204):
                return True, None

            if resp.status_code == 409 and self._mkcol_chain(mkcol_paths):
                logger.info(
                    "PUT %s returned 409 — MKCOL'd parents, retrying once",
                    label,
                )
                resp = _do_put()
                if resp.status_code in (200, 201, 204):
                    return True, None

            reason = self._format_http_failure(resp, prefix)
            logger.error(f"PUT {label} échoué ({url}): {reason}")
            return False, reason
        except requests.RequestException as exc:
            reason = f"{prefix}{_sanitize_request_error(exc)}"
            logger.error(f"PUT {label} échoué ({url}): {reason}")
            return False, reason

    @staticmethod
    def _format_http_failure(resp: requests.Response, prefix: str) -> str:
        """
        EN: Turn a non-2xx response into a short user-safe message. We
            include the body for 5xx so backend errors aren't reduced to
            an opaque "500" — but cap the length so leaked stack traces
            don't bloat the UI.
        FR: Formate une réponse non-2xx en message court. Pour les 5xx,
            on inclut le corps tronqué pour exposer le motif réel.
        """
        base = f"{prefix}WebDAV upload failed: {resp.status_code} {resp.reason or ''}".strip()
        if 500 <= resp.status_code < 600:
            try:
                body = resp.text.strip()
            except Exception:
                body = ""
            if body:
                snippet = body[:200].replace("\n", " ")
                if len(body) > 200:
                    snippet += "…"
                return f"{base} — {snippet}"
        return base

    def _mkcol_chain(self, paths: tuple[str, ...]) -> bool:
        """
        EN: MKCOL each path in order. Returns True if every step yielded a
            success-equivalent status (created or already-exists). Used to
            heal a 409 caused by a missing parent collection.
        FR: MKCOL chaque chemin dans l'ordre. Retourne True si chaque étape
            a réussi (créé ou déjà présent). Sert à corriger un 409 dû à
            un parent manquant.
        """
        for path in paths:
            url = urljoin(self.base_url, "/".join(quote(p, safe="") for p in path.split("/") if p))
            try:
                resp = self.session.request(
                    "MKCOL",
                    url,
                    timeout=self.config.timeout_seconds,
                )
            except requests.RequestException as exc:
                logger.warning(f"MKCOL chain failed at {path}: {exc}")
                return False
            if not self._is_mkcol_success(resp.status_code):
                logger.warning(
                    "MKCOL chain rejected at %s: %s %s",
                    path,
                    resp.status_code,
                    resp.reason,
                )
                return False
        return True

    def create_notebook_dir(self, notebook_slug: str) -> bool:
        """MKCOL /{notebook} — creates a notebook folder on the remote server."""
        url = self._url(notebook_slug)
        try:
            resp = self.session.request("MKCOL", url, timeout=self.config.timeout_seconds)
            return self._is_mkcol_success(resp.status_code)
        except requests.RequestException as e:
            logger.error(f"MKCOL failed ({url}): {e}")
            return False

    def create_note_dir(self, notebook_slug: str, note_slug: str) -> bool:
        """MKCOL /{notebook}/{note} — creates a note folder on the remote server."""
        url = self._url(notebook_slug, note_slug)
        try:
            resp = self.session.request("MKCOL", url, timeout=self.config.timeout_seconds)
            return self._is_mkcol_success(resp.status_code)
        except requests.RequestException as e:
            logger.error(f"MKCOL note failed ({url}): {e}")
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

                # EN: Hrefs come back URL-encoded (WsgiDAV percent-encodes
                #     non-ASCII like `é` → `%C3%A9`). The slugs we compare
                #     against (`note_slug`, `nb_slug`) are kept decoded, so
                #     we must decode here too — otherwise notes with accents
                #     in their title look "missing" and the engine kicks
                #     off a redundant MKCOL on every push, which masks real
                #     409 failures and inflates server load.
                # FR: Les hrefs sont URL-encodés. Les slugs comparés sont
                #     décodés — on décode ici, sinon les notes accentuées
                #     déclenchent un MKCOL inutile à chaque push.
                raw_name = href.rstrip("/").split("/")[-1]
                name = unquote(raw_name)

                display_name = response.findtext(".//D:displayname", "", ns) or name

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
                    "name": name,
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


def _make_local_conflict_copy(local: Note) -> Note:
    """
    EN: Build an independent "(conflit …)" copy of the local note so its
        unsynced edits survive when the chosen resolution would otherwise
        replace them with the remote version. Mirrors the copy the
        ``KEEP_BOTH`` strategy makes, but is created at the engine level so
        the safety net applies under *every* conflict strategy.
    FR: Crée une copie indépendante "(conflit …)" de la note locale pour
        préserver ses modifications non synchronisées quand la résolution
        choisie les remplacerait par la version distante.
    """
    conflict_copy = copy.deepcopy(local)
    conflict_copy.id = str(uuid.uuid4())
    ts = local.updated_at.strftime("%Y-%m-%d_%H-%M")
    conflict_copy.title = f"{local.title} (conflit {ts})"
    conflict_copy.sync_status = SyncStatus.LOCAL_ONLY
    return conflict_copy


class NexaNoteSyncEngine:
    """
    Moteur de synchronisation principal.
    Orchestre pull → diff → resolve → push.
    """

    def __init__(
        self,
        db: FileNoteStore,
        config: SyncConfig,
        dry_run: bool = False,
    ) -> None:
        self.db = db
        self.config = config
        self.client = WebDAVClient(config)
        self.resolver = ConflictResolver(config.conflict_strategy)
        # EN: Per-data-dir registry of adopted/ignored remote paths. Loaded
        #     up-front so a re-pull skips legacy files we've already decided
        #     about; saved at the end of every ``sync()`` call.
        # FR: Registre des chemins distants adoptés/ignorés. Chargé à l'init,
        #     sauvegardé à la fin de chaque ``sync()``.
        self.sync_state = SyncState.load(Path(db.data_dir))
        # EN: Dry-run builds the plan but performs no writes (no note files,
        #     no sync-state file, no remote PUTs, no sync log). The plan is
        #     populated as decisions are made; in dry-run that happens with
        #     every mutating call short-circuited.
        # FR: Le dry-run construit le plan sans aucune écriture.
        self.dry_run = dry_run
        # The active plan for the current session — created in ``sync()`` and
        # lazily in ``_pull``/``_push`` for standalone calls (e.g. tests).
        self.plan: Optional[SyncPlan] = None

    # ------------------------------------------------------------------
    # Dry-run guards — the single choke point for every state mutation.
    # In dry-run mode each of these is a no-op, which is what makes the
    # "dry-run never writes" guarantee easy to audit and to test.
    # ------------------------------------------------------------------

    def _ensure_plan(self) -> SyncPlan:
        if self.plan is None:
            self.plan = SyncPlan()
        return self.plan

    def _apply_save_note(self, note: Note, save_pages: bool = True) -> None:
        if not self.dry_run:
            self.db.save_note(note, save_pages=save_pages)

    def _apply_mark_adopted(self, remote_path: str, local_id: str) -> None:
        if not self.dry_run:
            self.sync_state.mark_adopted(remote_path, local_id)

    def _apply_mark_ignored(self, remote_path: str, reason: str) -> None:
        if not self.dry_run:
            self.sync_state.mark_ignored(remote_path, reason)

    def _apply_touch_ignored(self, remote_path: str) -> None:
        if not self.dry_run:
            self.sync_state.touch_ignored(remote_path)

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def sync(self) -> SyncReport:
        """
        EN: Run a full sync session: ping → pull → push. The ``SyncPlan`` is
            built as decisions are made; in dry-run no files, sync state, or
            remote resources are touched and no log is written. Otherwise the
            sync state is persisted (even on error, so partial "ignored"
            decisions survive) and a sanitized log is written at the end.
        FR: Lance une session complète : ping → pull → push. Le plan est
            construit au fil des décisions ; en dry-run rien n'est écrit.
        """
        report = SyncReport()
        report.dry_run = self.dry_run
        self.plan = SyncPlan()
        report.plan = self.plan
        logger.info(
            "Début de la synchronisation NexaNote%s",
            " (dry-run)" if self.dry_run else "",
        )

        # Vérifier la connexion
        if not self.client.ping():
            msg = f"Impossible de joindre le serveur : {self.config.server_url}"
            logger.error(msg)
            report.errors.append(msg)
            report.finish()
            self._write_log(report)
            return report

        try:
            # 1. Pull depuis le serveur
            self._pull(report)
            # 2. Push vers le serveur
            self._push(report)
        except Exception as e:
            logger.exception("Erreur inattendue pendant la sync")
            report.errors.append(str(e))
        finally:
            # Persist the registry even when sync errored — we still want
            # to remember any "ignored" decisions made during the partial
            # run so the next sync skips those remote paths immediately.
            # Skipped entirely in dry-run so state on disk is never touched.
            if not self.dry_run:
                try:
                    self.sync_state.save()
                except Exception:
                    logger.exception("could not persist sync state")

        report.finish()
        self._write_log(report)
        logger.info(report.summary())
        return report

    def _write_log(self, report: SyncReport) -> None:
        """
        EN: Write the sanitized diagnostic log, unless this was a dry-run
            (which must not write any files). Never raises.
        FR: Écrit le journal de diagnostic assaini, sauf en dry-run.
        """
        if self.dry_run:
            return
        try:
            write_sync_log(
                self.db.data_dir, report, self.plan, dry_run=self.dry_run
            )
        except Exception:
            logger.exception("could not write sync log")

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
        self._ensure_plan()
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
        """
        EN: Pull a single note from the server. Resolves the local target
            in three stages — note id, then remote-path mapping, then
            (only for non-legacy ids) a fresh adoption. Notes whose remote
            id looks legacy/manual (no real NexaNote frontmatter id) get
            recorded in the ignore registry on first encounter so we never
            re-import them. This is the core of the duplicate-creation fix.
        FR: Récupère une note précise depuis le serveur. Résolution en
            trois étapes (id → remote_path → adoption). Les notes dont
            l'id ressemble à un fichier .md hérité sont enregistrées dans
            le registre "ignoré" pour ne pas être réimportées.
        """
        plan = self._ensure_plan()
        remote_path = _remote_path(nb_slug, note_slug)

        # Step 1: short-circuit on previously ignored paths. Touch the entry
        # so its `last_seen` timestamp reflects the latest sync — useful for
        # "still seeing this file" diagnostics.
        if self.sync_state.is_ignored(remote_path):
            self._apply_touch_ignored(remote_path)
            report.notes_ignored_legacy += 1
            plan.add_ignore(
                remote_path,
                self.sync_state.get_ignored_reason(remote_path)
                or "previously ignored",
            )
            return

        meta = self.client.get_note_meta(nb_slug, note_slug)
        if not meta:
            return

        note_id = meta.get("id")
        if not note_id:
            # No id at all in the server payload — nothing safe to do but
            # remember to skip it. Legacy/manual file with no NexaNote
            # metadata; importing would invent a fresh id every time.
            reason = "remote note.json carried no id"
            self._apply_mark_ignored(remote_path, reason)
            report.notes_ignored_legacy += 1
            plan.add_ignore(remote_path, reason)
            logger.info(
                "  ⊘ Legacy/manual note ignored (%s): %s",
                reason,
                remote_path,
            )
            return

        legacy_id = _is_legacy_remote_id(note_id)

        # Step 2: try to find the local twin. First by id (frontmatter or
        # previously-adopted synthetic id), then by the remote-path mapping
        # we built up over previous sync sessions.
        local_note = self.db.get_note(note_id, load_pages=True)
        matched_via_remote_path = False
        adopted_local_id: Optional[str] = None
        if local_note is None:
            adopted_local_id = self.sync_state.get_adopted_local_id(remote_path)
            if adopted_local_id and adopted_local_id != note_id:
                local_note = self.db.get_note(
                    adopted_local_id, load_pages=True
                )
                if local_note is not None:
                    matched_via_remote_path = True

        # If a previous adoption mapped this remote_path to a local id that
        # is no longer in the store, treat it as "user purged it" — we MUST
        # NOT silently re-import, since that would be a duplicate. Record
        # an ignore marker (with a clear reason) and bail out.
        if (
            local_note is None
            and adopted_local_id is not None
            and adopted_local_id != note_id
        ):
            reason = "previously adopted local note no longer present"
            self._apply_mark_ignored(remote_path, reason)
            report.notes_ignored_legacy += 1
            plan.add_ignore(remote_path, reason)
            logger.info(
                "  ⊘ Legacy/manual note ignored (%s): %s [id=%s]",
                reason,
                remote_path,
                note_id,
            )
            return

        # Step 3: if we still have no match AND the remote id is legacy,
        # do not adopt — record an ignore marker so future pulls bail out
        # immediately, and surface it in the report.
        if local_note is None and legacy_id:
            reason = (
                "no NexaNote frontmatter id; legacy/manual Markdown file"
            )
            self._apply_mark_ignored(remote_path, reason)
            report.notes_ignored_legacy += 1
            plan.add_ignore(remote_path, reason)
            logger.info(
                "  ⊘ Legacy/manual note ignored (%s): %s [id=%s]",
                reason,
                remote_path,
                note_id,
            )
            return

        # Remote_path match with a different id: we've already adopted this
        # path under another local id. Refuse to merge content (the conflict
        # resolver requires same-id notes), but refresh the mapping so the
        # registry stays current. The local copy is canonical here.
        if matched_via_remote_path and local_note is not None:
            self._apply_mark_adopted(remote_path, local_note.id)
            plan.add_warning(
                f"remote path changed id on server; kept local note "
                f"{local_note.id[:8]} for {remote_path}"
            )
            logger.info(
                "  ↺ Remote_path match (id mismatch — keeping local %s): %s",
                local_note.id,
                remote_path,
            )
            return

        # From here we know we want to adopt. Pull the ink pages now —
        # we skipped them earlier so the legacy-ignore branch never burned
        # extra HTTP calls on files we won't import.
        ink_pages: dict[int, dict] = {}
        for page_data in meta.get("pages", []):
            num = page_data["page_number"]
            ink = self.client.get_ink_page(nb_slug, note_slug, num)
            if ink:
                ink_pages[num] = ink

        remote_note = _deserialize_note(meta, ink_pages)

        if local_note is None:
            # Fresh adoption. The id is non-legacy (filtered above) so it
            # is safe to use as-is.
            self._apply_save_note(remote_note)
            self._apply_mark_adopted(remote_path, remote_note.id)
            report.notes_pulled += 1
            plan.add_pull(remote_note.id, remote_note.title)
            report.events.append(SyncEvent(
                SyncEventType.NOTE_PULLED,
                f"Nouvelle note importée : {remote_note.title}",
                note_id=note_id,
            ))
            logger.info(f"  ← Importée : {remote_note.title}")

        elif local_note.sync_status == SyncStatus.MODIFIED:
            # Conflict path — local has unsynced edits and a remote copy
            # exists. Snapshot the local version *before* resolving so its
            # edits can be preserved even when the chosen strategy would
            # overwrite them.
            local_snapshot = copy.deepcopy(local_note)
            result = self.resolver.resolve(local_note, remote_note)

            # A genuine conflict = both sides changed. The resolver reports
            # no conflict when the timestamps match (identical versions).
            is_real_conflict = result.had_conflict() or (
                local_snapshot.updated_at != remote_note.updated_at
            )
            local_won = local_snapshot.updated_at >= remote_note.updated_at

            # Conflict safety net: never silently drop local edits. If the
            # remote version won and the strategy kept no copy of the local
            # one, synthesise one so both versions survive on disk.
            conflict_copy = result.conflict_copy
            if is_real_conflict and conflict_copy is None and not local_won:
                conflict_copy = _make_local_conflict_copy(local_snapshot)

            self._apply_save_note(result.winner)
            if conflict_copy is not None:
                self._apply_save_note(conflict_copy)

            self._apply_mark_adopted(remote_path, result.winner.id)
            report.notes_pulled += 1
            report.conflicts_resolved += 1
            if is_real_conflict:
                plan.add_conflict(
                    note_id,
                    local_snapshot.title,
                    result.message,
                    preserved_both=conflict_copy is not None,
                )
            report.events.append(SyncEvent(
                SyncEventType.CONFLICT_RESOLVED,
                result.message,
                note_id=note_id,
            ))
            logger.info(f"  ⚡ Conflit résolu : {local_snapshot.title} — {result.message}")

        else:
            # Pas de modification locale — appliquer la version distante si plus récente
            if remote_note.updated_at > local_note.updated_at:
                self._apply_save_note(remote_note)
                report.notes_pulled += 1
                plan.add_pull(remote_note.id, remote_note.title)
                logger.info(f"  ↓ Mise à jour : {remote_note.title}")
            # Always refresh the mapping so future pulls go fast even when
            # the content hasn't changed.
            self._apply_mark_adopted(remote_path, local_note.id)

    # ------------------------------------------------------------------
    # PUSH — envoyer les notes modifiées localement
    # ------------------------------------------------------------------

    def _push(self, report: SyncReport) -> None:
        """
        Envoie toutes les notes marquées MODIFIED ou LOCAL_ONLY
        vers le serveur WebDAV.
        """
        plan = self._ensure_plan()
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
            # Dry-run records the intent to push but performs no network PUT
            # — it must never modify the remote server either.
            if self.dry_run:
                plan.add_push(note.id, note.title)
                report.notes_pushed += 1
                continue
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

        try:
            if notebook:
                nb_slug = _notebook_to_slug(notebook)
            else:
                # EN: Notes without a notebook go into the default fallback folder.
                # FR: Les notes sans carnet sont placées dans le dossier de repli par défaut.
                nb_slug = DEFAULT_NOTEBOOK_SLUG
                logger.debug(f"Note {note.title!r} has no notebook → using '{nb_slug}' folder")
            note_slug = _note_to_slug(note)
        except Exception as exc:
            # Slug computation only fails on truly malformed notes (e.g.
            # missing id), but if it ever does we want a useful reason
            # instead of a raw traceback bubbling up to the sync report.
            msg = f"path generation failed: {type(exc).__name__}: {exc}"
            logger.exception("Could not compute push path for note %s", note.id)
            report.errors.append(f"Échec partiel push : {note.title} — {msg}")
            return

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
        try:
            meta_payload = _serialize_note_meta(note)
        except Exception as exc:
            logger.exception("note.json serialization failed for %s", note.id)
            reason = f"note.json: serialization failed: {type(exc).__name__}: {exc}"
            report.errors.append(f"Échec partiel push : {note.title} — {reason}")
            return
        meta_ok, meta_reason = self.client.put_note_meta(
            nb_slug, note_slug, meta_payload
        )

        # PUT page_N.ink pour chaque page
        pages_ok = True
        page_reasons: list[str] = []
        for page in note.pages:
            try:
                ink_payload = _serialize_ink_page(page)
            except Exception as exc:
                logger.exception(
                    "page_%d.ink serialization failed for note %s",
                    page.page_number,
                    note.id,
                )
                pages_ok = False
                page_reasons.append(
                    f"page {page.page_number}: serialization failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            ok, page_reason = self.client.put_ink_page(
                nb_slug, note_slug, page.page_number, ink_payload,
            )
            if not ok:
                pages_ok = False
                if page_reason:
                    page_reasons.append(page_reason)

        if meta_ok and pages_ok:
            # Marquer comme SYNCED
            note.sync_status = SyncStatus.SYNCED
            self._apply_save_note(note, save_pages=False)
            report.notes_pushed += 1
            self._ensure_plan().add_push(note.id, note.title)
            report.events.append(SyncEvent(
                SyncEventType.NOTE_PUSHED,
                f"Note envoyée : {note.title}",
                note_id=note.id,
            ))
            logger.info(f"  → Envoyée : {note.title}")
        else:
            reasons: list[str] = []
            if not meta_ok and meta_reason:
                reasons.append(meta_reason)
            reasons.extend(page_reasons)
            detail = "; ".join(reasons) if reasons else "unknown error"
            msg = f"Échec partiel push : {note.title} — {detail}"
            report.errors.append(msg)
            logger.warning(msg)
