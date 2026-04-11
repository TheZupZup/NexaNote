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

from cheroot import wsgi
from wsgidav.wsgidav_app import WsgiDAVApp

from nexanote.storage.database import NexaNoteDB
from nexanote.sync.webdav_provider import NexaNoteDAVProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nexanote.server")


def _hash_password(password: str) -> str:
    """Hash SHA-256 simple — pour production utiliser bcrypt."""
    return hashlib.sha256(password.encode()).hexdigest()


def build_app(
    db: NexaNoteDB,
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
    db_path = data_dir / "nexanote.db"

    logger.info(f"Base de données : {db_path}")
    db = NexaNoteDB(db_path)

    # Créer un carnet de démonstration si la DB est vide
    notebooks = db.list_notebooks()
    if not notebooks:
        from nexanote.models.note import Note, Notebook, NoteType
        nb = Notebook(name="Mon premier carnet", color="#6366f1")
        db.save_notebook(nb)
        note = Note(
            notebook_id=nb.id,
            title="Bienvenue dans NexaNote",
            note_type=NoteType.TYPED,
        )
        page = note.add_page(template="lined")
        page.typed_content = (
            "# Bienvenue dans NexaNote\n\n"
            "Cette note a été créée automatiquement.\n"
            "Connecte ton app Flutter à ce serveur WebDAV pour commencer à synchroniser tes notes.\n"
        )
        db.save_note(note)
        logger.info("Données de démonstration créées")

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
    logger.info(f"  Identifiants : {username} / {password}")
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
