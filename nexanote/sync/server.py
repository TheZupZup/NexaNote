"""
NexaNote — Serveur WebDAV
Lance le serveur de synchronisation NexaNote.

Usage :
    python -m nexanote.sync.server
    python -m nexanote.sync.server --port 8080 --host 0.0.0.0
    python -m nexanote.sync.server --data-dir ~/nexanote-data --no-auth
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Optional

from cheroot import wsgi
from wsgidav.wsgidav_app import WsgiDAVApp

from nexanote.models.note import Note, Notebook, NoteType, SyncStatus
from nexanote.storage import FileNoteStore, create_store, run_migration
from nexanote.sync.webdav_provider import NexaNoteDAVProvider

# EN: Slug for the fallback notebook used to host notes that aren't assigned
#     to any user-created notebook. Mirrors the constant in sync.client so
#     push targets always have a valid parent collection on the server.
# FR: Slug du carnet de repli pour les notes sans carnet attribué. Identique
#     au constant côté client : garantit un parent valide pour les PUT.
DEFAULT_NOTEBOOK_NAME = "Uncategorized"
DEFAULT_NOTEBOOK_ID_PREFIX = "00000000"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nexanote.server")


def _hash_password(password: str) -> str:
    """Hash SHA-256 simple — pour production utiliser bcrypt."""
    return hashlib.sha256(password.encode()).hexdigest()


def ensure_storage_layout(db: FileNoteStore) -> None:
    """
    EN: Make sure the on-disk directories WebDAV expects (notes/, drawings/,
        notebooks/) exist. ``FileNoteStore.__init__`` already creates them,
        but call this defensively before serving so a wiped data dir during
        runtime is recreated rather than throwing on the first request.
    FR: S'assure que les dossiers attendus par WebDAV existent. Idempotent.
    """
    for d in (db.notes_dir, db.drawings_dir, db.notebooks_dir):
        d.mkdir(parents=True, exist_ok=True)


def ensure_default_notebook(db: FileNoteStore) -> Notebook:
    """
    EN: Ensure the fallback "Uncategorized" notebook exists. Notes pushed
        without a notebook id land here, so its parent collection must be
        present on the server before the client's first PUT.
    FR: Garantit que le carnet "Uncategorized" existe. Les notes sans
        carnet y sont rangées : son dossier doit déjà être créé côté serveur.
    """
    for nb in db.list_notebooks():
        if nb.id.startswith(DEFAULT_NOTEBOOK_ID_PREFIX):
            return nb
    notebook = Notebook(
        id=f"{DEFAULT_NOTEBOOK_ID_PREFIX}-0000-0000-0000-000000000000",
        name=DEFAULT_NOTEBOOK_NAME,
        color="#6b7280",
        sync_status=SyncStatus.SYNCED,
    )
    db.save_notebook(notebook)
    logger.info("Default notebook created: %s", DEFAULT_NOTEBOOK_NAME)
    return notebook


def seed_demo_data(db: FileNoteStore) -> Optional[Note]:
    """
    EN: First-run-only demo content. Creates a sample notebook and a tutorial
        note marked as SYNCED so it lives on this server but never gets
        re-pushed by a sync engine sharing this data dir. Pull copies it
        to clients normally; once a client edits it, the local copy flips
        to MODIFIED and the next push round-trips like any user note.

        Skipped if any non-fallback notebook already exists. Returns the
        seeded note (or None if nothing was seeded) — useful for tests.

    FR: Contenu de démo créé une seule fois. Marqué SYNCED pour qu'il ne
        soit jamais re-poussé par un moteur de sync partageant ce data dir.
    """
    has_user_notebook = any(
        not nb.id.startswith(DEFAULT_NOTEBOOK_ID_PREFIX)
        for nb in db.list_notebooks()
    )
    if has_user_notebook:
        return None

    notebook = Notebook(
        name="Mon premier carnet",
        color="#6366f1",
        sync_status=SyncStatus.SYNCED,
    )
    db.save_notebook(notebook)

    note = Note(
        notebook_id=notebook.id,
        title="Bienvenue dans NexaNote",
        note_type=NoteType.TYPED,
    )
    page = note.add_page(template="lined")
    page.typed_content = (
        "# Bienvenue dans NexaNote\n\n"
        "Cette note a été créée automatiquement.\n"
        "Connecte ton app Flutter à ce serveur WebDAV pour commencer à synchroniser tes notes.\n"
    )
    # Mark SYNCED so a sync engine sharing this data dir doesn't pick the
    # demo note up for push (it already lives on this server).
    note.sync_status = SyncStatus.SYNCED
    db.save_note(note)
    logger.info("Données de démonstration créées")
    return note


def build_app(
    db: FileNoteStore,
    username: str = "nexanote",
    password: str = "nexanote",
    verbose: bool = False,
) -> WsgiDAVApp:
    """
    Construit l'application WSGI WebDAV avec le provider NexaNote.
    """
    provider = NexaNoteDAVProvider(db)

    config = {
        # Notre provider custom
        "provider_mapping": {"/": provider},

        # Authentification HTTP Basic
        # En production : utiliser HTTPS + mots de passe forts
        "http_authenticator": {
            "domain_controller": None,  # Utilise le domain controller par défaut
            "accept_basic": True,
            "accept_digest": False,
            "default_to_digest": False,
        },
        "simple_dc": {
            "user_mapping": {
                "*": {
                    username: {
                        "password": password,
                    }
                }
            }
        },

        # Options WebDAV
        "lock_storage": True,      # Activer le verrouillage DAV (LOCK/UNLOCK)
        "property_manager": True,  # Activer les propriétés DAV étendues

        # Logging
        "verbose": 2 if verbose else 1,
        "logging": {
            "enable_loggers": ["nexanote"],
        },

        # Middleware
        "middleware_stack": [
            "wsgidav.error_printer.ErrorPrinter",
            "wsgidav.http_authenticator.HTTPAuthenticator",
            "wsgidav.dir_browser.WsgiDavDirBrowser",
            "wsgidav.request_resolver.RequestResolver",
        ],
    }

    if importlib.util.find_spec("wsgidav.debug_filter"):
        config["middleware_stack"].insert(0, "wsgidav.debug_filter.WsgiDavDebugFilter")

    return WsgiDAVApp(config)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path = Path.home() / ".nexanote",
    username: str = "nexanote",
    password: str = "nexanote",
    verbose: bool = False,
) -> None:
    """Démarre le serveur WebDAV NexaNote."""

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Data directory : {data_dir}")
    # Run any pending SQLite → file migration before opening the store.
    migration_report = run_migration(data_dir)
    if migration_report.ran:
        logger.info(migration_report.summary())
    db = create_store(data_dir)

    # Ensure required WebDAV directories exist + the fallback notebook is
    # present, so the very first sync push has valid parent collections.
    ensure_storage_layout(db)
    ensure_default_notebook(db)
    seed_demo_data(db)

    app = build_app(db, username=username, password=password, verbose=verbose)

    server = wsgi.Server(
        bind_addr=(host, port),
        wsgi_app=app,
        numthreads=10,
        request_queue_size=50,
    )

    logger.info("=" * 55)
    logger.info("  NexaNote WebDAV Server")
    logger.info("=" * 55)
    logger.info(f"  URL      : http://{host}:{port}/")
    logger.info(f"  User     : {username}")
    logger.info(f"  Data dir : {data_dir}")
    logger.info("=" * 55)
    logger.info("  Pour se connecter depuis un client WebDAV :")
    logger.info(f"  URL : http://{host}:{port}/")
    # EN: The password is intentionally NOT logged. Container logs are often
    #     persisted, forwarded, or shared in bug reports — printing the
    #     credential in plaintext would leak it. Users configure the
    #     password via --password / NEXANOTE_PASSWORD; the server prints
    #     only the username so operators can confirm which account is live.
    # FR: Le mot de passe n'est jamais journalisé — les logs étant souvent
    #     persistés/transmis, l'afficher ferait fuiter le secret.
    logger.info(f"  Utilisateur : {username} (mot de passe configuré)")
    logger.info("=" * 55)
    logger.info("  Ctrl+C pour arrêter le serveur")

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Arrêt du serveur...")
        server.stop()
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NexaNote WebDAV Sync Server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute")
    parser.add_argument("--port", type=int, default=8765, help="Port d'écoute")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".nexanote",
        help="Dossier de données (DB + fichiers)",
    )
    parser.add_argument("--username", default="nexanote", help="Nom d'utilisateur WebDAV")
    parser.add_argument("--password", default="nexanote", help="Mot de passe WebDAV")
    parser.add_argument("--verbose", action="store_true", help="Logs détaillés")

    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        username=args.username,
        password=args.password,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
