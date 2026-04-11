"""
NexaNote — API REST (FastAPI)
Interface HTTP que l'app Flutter consomme pour toutes les opérations.

Routes :
  GET    /health                      → statut du serveur
  GET    /notebooks                   → liste des carnets
  POST   /notebooks                   → créer un carnet
  GET    /notebooks/{id}              → détails d'un carnet
  PUT    /notebooks/{id}              → modifier un carnet
  DELETE /notebooks/{id}              → supprimer un carnet

  GET    /notes                       → liste des notes (filtrable)
  POST   /notes                       → créer une note
  GET    /notes/{id}                  → détails d'une note (avec pages)
  PUT    /notes/{id}                  → modifier une note
  DELETE /notes/{id}                  → suppression logique
  POST   /notes/{id}/restore          → restaurer depuis la corbeille
  POST   /notes/{id}/duplicate        → dupliquer une note

  GET    /notes/{id}/pages/{num}      → récupérer une page
  PUT    /notes/{id}/pages/{num}/ink  → sauvegarder les strokes d'une page
  PUT    /notes/{id}/pages/{num}/text → sauvegarder le contenu texte

  POST   /sync/trigger                → déclencher une sync WebDAV
  GET    /sync/status                 → état de la dernière sync

  GET    /stats                       → statistiques globales
  GET    /search?q=...                → recherche par titre
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from nexanote.models.note import (
    InkStroke, Note, Notebook, NoteType, Page, Point, SyncStatus
)
from nexanote.storage.database import NexaNoteDB
from nexanote.sync.client import NexaNoteSyncEngine, SyncConfig, SyncReport

logger = logging.getLogger("nexanote.api")

# ---------------------------------------------------------------------------
# Schémas Pydantic (validation + sérialisation)
# ---------------------------------------------------------------------------

class PointSchema(BaseModel):
    x: float
    y: float
    pressure: float = 0.5
    ts: int = 0


class StrokeSchema(BaseModel):
    id: str
    color: str = "#000000"
    width: float = 2.0
    tool: str = "pen"
    points: list[PointSchema]
    created_at: Optional[str] = None


class PageSchema(BaseModel):
    page_number: int
    template: str = "blank"
    width_px: float = 1404.0
    height_px: float = 1872.0
    typed_content: str = ""
    strokes: list[StrokeSchema] = Field(default_factory=list)
    updated_at: Optional[str] = None


class NoteCreateSchema(BaseModel):
    title: str = "Sans titre"
    note_type: str = "typed"
    notebook_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    template: str = "blank"


class NoteUpdateSchema(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None
    is_pinned: Optional[bool] = None
    notebook_id: Optional[str] = None


class NoteSchema(BaseModel):
    id: str
    title: str
    note_type: str
    notebook_id: Optional[str]
    tags: list[str]
    is_pinned: bool
    is_archived: bool
    is_deleted: bool
    sync_status: str
    page_count: int
    created_at: str
    updated_at: str
    pages: Optional[list[PageSchema]] = None


class NotebookCreateSchema(BaseModel):
    name: str = "Nouveau carnet"
    description: str = ""
    color: str = "#6366f1"
    icon: str = "notebook"
    parent_id: Optional[str] = None


class NotebookUpdateSchema(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None


class NotebookSchema(BaseModel):
    id: str
    name: str
    description: str
    color: str
    icon: str
    parent_id: Optional[str]
    is_archived: bool
    sync_status: str
    created_at: str
    updated_at: str


class InkUpdateSchema(BaseModel):
    strokes: list[StrokeSchema]


class TextUpdateSchema(BaseModel):
    typed_content: str


class SyncConfigSchema(BaseModel):
    server_url: str
    username: str = "nexanote"
    password: str = "nexanote"
    conflict_strategy: str = "merge_strokes"


class SyncReportSchema(BaseModel):
    success: bool
    notes_pulled: int
    notes_pushed: int
    conflicts_resolved: int
    errors: list[str]
    duration_seconds: float
    summary: str


# ---------------------------------------------------------------------------
# Sérialiseurs
# ---------------------------------------------------------------------------

def _notebook_to_schema(nb: Notebook) -> NotebookSchema:
    return NotebookSchema(
        id=nb.id,
        name=nb.name,
        description=nb.description,
        color=nb.color,
        icon=nb.icon,
        parent_id=nb.parent_id,
        is_archived=nb.is_archived,
        sync_status=nb.sync_status.value,
        created_at=nb.created_at.isoformat(),
        updated_at=nb.updated_at.isoformat(),
    )


def _note_to_schema(note: Note, include_pages: bool = False) -> NoteSchema:
    pages = None
    if include_pages:
        pages = [_page_to_schema(p) for p in note.pages]
    return NoteSchema(
        id=note.id,
        title=note.title,
        note_type=note.note_type.value,
        notebook_id=note.notebook_id,
        tags=note.tags,
        is_pinned=note.is_pinned,
        is_archived=note.is_archived,
        is_deleted=note.is_deleted,
        sync_status=note.sync_status.value,
        page_count=note.page_count(),
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
        pages=pages,
    )


def _page_to_schema(page: Page) -> PageSchema:
    return PageSchema(
        page_number=page.page_number,
        template=page.template,
        width_px=page.width_px,
        height_px=page.height_px,
        typed_content=page.typed_content,
        updated_at=page.updated_at.isoformat(),
        strokes=[
            StrokeSchema(
                id=s.id,
                color=s.color,
                width=s.width,
                tool=s.tool,
                created_at=s.created_at.isoformat(),
                points=[
                    PointSchema(x=p.x, y=p.y, pressure=p.pressure, ts=p.timestamp_ms)
                    for p in s.points
                ],
            )
            for s in page.strokes
        ],
    )


# ---------------------------------------------------------------------------
# App FastAPI
# ---------------------------------------------------------------------------

def create_app(db: NexaNoteDB) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("NexaNote API démarrée")
        yield
        db.close()
        logger.info("NexaNote API arrêtée")

    app = FastAPI(
        title="NexaNote API",
        description="API REST pour l'app NexaNote",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # État sync
    _last_sync_report: dict = {}
    _sync_config: dict = {}

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        stats = db.get_stats()
        return {
            "status": "ok",
            "version": "0.1.0",
            "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Notebooks
    # ------------------------------------------------------------------

    @app.get("/notebooks", response_model=list[NotebookSchema])
    def list_notebooks(include_archived: bool = False):
        return [
            _notebook_to_schema(nb)
            for nb in db.list_notebooks(include_archived=include_archived)
        ]

    @app.post("/notebooks", response_model=NotebookSchema, status_code=201)
    def create_notebook(data: NotebookCreateSchema):
        nb = Notebook(
            name=data.name,
            description=data.description,
            color=data.color,
            icon=data.icon,
            parent_id=data.parent_id,
        )
        db.save_notebook(nb)
        logger.info(f"Carnet créé : {nb.name}")
        return _notebook_to_schema(nb)

    @app.get("/notebooks/{notebook_id}", response_model=NotebookSchema)
    def get_notebook(notebook_id: str):
        nb = db.get_notebook(notebook_id)
        if not nb:
            raise HTTPException(404, f"Carnet {notebook_id} introuvable")
        return _notebook_to_schema(nb)

    @app.put("/notebooks/{notebook_id}", response_model=NotebookSchema)
    def update_notebook(notebook_id: str, data: NotebookUpdateSchema):
        nb = db.get_notebook(notebook_id)
        if not nb:
            raise HTTPException(404, f"Carnet {notebook_id} introuvable")
        if data.name is not None:
            nb.name = data.name
        if data.description is not None:
            nb.description = data.description
        if data.color is not None:
            nb.color = data.color
        if data.icon is not None:
            nb.icon = data.icon
        nb.touch()
        db.save_notebook(nb)
        return _notebook_to_schema(nb)

    @app.delete("/notebooks/{notebook_id}", status_code=204)
    def delete_notebook(notebook_id: str):
        nb = db.get_notebook(notebook_id)
        if not nb:
            raise HTTPException(404, f"Carnet {notebook_id} introuvable")
        db.delete_notebook(notebook_id)

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    @app.get("/notes", response_model=list[NoteSchema])
    def list_notes(
        notebook_id: Optional[str] = None,
        include_deleted: bool = False,
        include_archived: bool = False,
        search: Optional[str] = Query(None, alias="q"),
    ):
        notes = db.list_notes(
            notebook_id=notebook_id,
            include_deleted=include_deleted,
            include_archived=include_archived,
            search_title=search,
        )
        return [_note_to_schema(n) for n in notes]

    @app.post("/notes", response_model=NoteSchema, status_code=201)
    def create_note(data: NoteCreateSchema):
        if data.notebook_id:
            nb = db.get_notebook(data.notebook_id)
            if not nb:
                raise HTTPException(404, f"Carnet {data.notebook_id} introuvable")

        note = Note(
            title=data.title,
            note_type=NoteType(data.note_type),
            notebook_id=data.notebook_id,
            tags=data.tags,
        )
        note.add_page(template=data.template)
        db.save_note(note)
        logger.info(f"Note créée : {note.title}")
        return _note_to_schema(note)

    @app.get("/notes/{note_id}", response_model=NoteSchema)
    def get_note(note_id: str, pages: bool = True):
        note = db.get_note(note_id, load_pages=pages)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        return _note_to_schema(note, include_pages=pages)

    @app.put("/notes/{note_id}", response_model=NoteSchema)
    def update_note(note_id: str, data: NoteUpdateSchema):
        note = db.get_note(note_id, load_pages=False)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        if data.title is not None:
            note.title = data.title
        if data.tags is not None:
            note.tags = data.tags
        if data.is_pinned is not None:
            note.is_pinned = data.is_pinned
        if data.notebook_id is not None:
            note.notebook_id = data.notebook_id
        note.touch()
        db.save_note(note, save_pages=False)
        return _note_to_schema(note)

    @app.delete("/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        note = db.get_note(note_id, load_pages=False)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        note.soft_delete()
        db.save_note(note, save_pages=False)

    @app.post("/notes/{note_id}/restore", response_model=NoteSchema)
    def restore_note(note_id: str):
        note = db.get_note(note_id, load_pages=False)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        note.restore()
        db.save_note(note, save_pages=False)
        return _note_to_schema(note)

    @app.post("/notes/{note_id}/duplicate", response_model=NoteSchema, status_code=201)
    def duplicate_note(note_id: str):
        import copy
        note = db.get_note(note_id, load_pages=True)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        dupe = copy.deepcopy(note)
        # Nouveau ID et titre pour la copie
        from nexanote.models.note import _new_id, _now
        dupe.id = _new_id()
        dupe.title = f"{note.title} (copie)"
        dupe.sync_status = SyncStatus.LOCAL_ONLY
        dupe.created_at = _now()
        dupe.updated_at = _now()
        # Réinitialiser les IDs de pages
        for page in dupe.pages:
            page.id = _new_id()
            page.note_id = dupe.id
        db.save_note(dupe)
        logger.info(f"Note dupliquée : {dupe.title}")
        return _note_to_schema(dupe)

    # ------------------------------------------------------------------
    # Pages — encre et texte
    # ------------------------------------------------------------------

    @app.get("/notes/{note_id}/pages/{page_num}", response_model=PageSchema)
    def get_page(note_id: str, page_num: int):
        note = db.get_note(note_id, load_pages=True)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        page = note.get_page(page_num)
        if not page:
            raise HTTPException(404, f"Page {page_num} introuvable dans la note {note_id}")
        return _page_to_schema(page)

    @app.put("/notes/{note_id}/pages/{page_num}/ink", response_model=PageSchema)
    def update_ink(note_id: str, page_num: int, data: InkUpdateSchema):
        """Remplace tous les strokes d'une page — appelé après chaque session d'écriture."""
        note = db.get_note(note_id, load_pages=True)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        page = note.get_page(page_num)
        if not page:
            raise HTTPException(404, f"Page {page_num} introuvable")

        new_strokes = []
        for s in data.strokes:
            points = [
                Point(x=p.x, y=p.y, pressure=p.pressure, timestamp_ms=p.ts)
                for p in s.points
            ]
            stroke = InkStroke(
                id=s.id,
                color=s.color,
                width=s.width,
                tool=s.tool,
                points=points,
            )
            new_strokes.append(stroke)

        page.strokes = new_strokes
        page.touch()
        note.touch()
        db.save_page(page)
        db.save_note(note, save_pages=False)
        return _page_to_schema(page)

    @app.put("/notes/{note_id}/pages/{page_num}/text", response_model=PageSchema)
    def update_text(note_id: str, page_num: int, data: TextUpdateSchema):
        """Met à jour le contenu texte/markdown d'une page."""
        note = db.get_note(note_id, load_pages=True)
        if not note:
            raise HTTPException(404, f"Note {note_id} introuvable")
        page = note.get_page(page_num)
        if not page:
            raise HTTPException(404, f"Page {page_num} introuvable")

        page.typed_content = data.typed_content
        page.touch()
        note.touch()
        db.save_page(page)
        db.save_note(note, save_pages=False)
        return _page_to_schema(page)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    @app.post("/sync/configure")
    def configure_sync(config: SyncConfigSchema):
        """Configure les paramètres de connexion WebDAV."""
        _sync_config.update(config.model_dump())
        return {"status": "configured", "server_url": config.server_url}

    @app.post("/sync/trigger", response_model=SyncReportSchema)
    def trigger_sync():
        """Déclenche une synchronisation manuelle."""
        if not _sync_config.get("server_url"):
            raise HTTPException(400, "Sync non configurée — appeler POST /sync/configure d'abord")

        from nexanote.sync.client import ConflictStrategy
        config = SyncConfig(
            server_url=_sync_config["server_url"],
            username=_sync_config.get("username", "nexanote"),
            password=_sync_config.get("password", "nexanote"),
            conflict_strategy=ConflictStrategy(
                _sync_config.get("conflict_strategy", "merge_strokes")
            ),
        )

        engine = NexaNoteSyncEngine(db, config)
        report = engine.sync()

        result = SyncReportSchema(
            success=report.success(),
            notes_pulled=report.notes_pulled,
            notes_pushed=report.notes_pushed,
            conflicts_resolved=report.conflicts_resolved,
            errors=report.errors,
            duration_seconds=report.duration_seconds(),
            summary=report.summary(),
        )
        _last_sync_report.update(result.model_dump())
        return result

    @app.get("/sync/status")
    def sync_status():
        return _last_sync_report or {"status": "never_synced"}

    # ------------------------------------------------------------------
    # Stats et recherche
    # ------------------------------------------------------------------

    @app.get("/stats")
    def get_stats():
        return db.get_stats()

    @app.get("/search", response_model=list[NoteSchema])
    def search(q: str = Query(..., min_length=1)):
        notes = db.list_notes(search_title=q)
        return [_note_to_schema(n) for n in notes]

    # ------------------------------------------------------------------
    # Stockage
    # ------------------------------------------------------------------

    @app.get("/storage")
    def get_storage_info():
        import os
        data_dir = str(db.db_path.parent)
        db_size = os.path.getsize(db.db_path) if db.db_path.exists() else 0
        return {
            "data_dir": data_dir,
            "db_path": str(db.db_path),
            "db_size_mb": round(db_size / 1024 / 1024, 2),
        }

    return app
