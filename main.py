"""
NexaNote — Point d'entrée principal

Lance les deux serveurs en parallèle :
  - Serveur WebDAV  sur le port 8765  (pour Nextcloud, NAS, rclone…)
  - API REST        sur le port 8766  (pour l'app Flutter)

Usage :
    python main.py
    python main.py --webdav-port 8765 --api-port 8766
    python main.py --host 0.0.0.0 --data-dir ~/nexanote-data
    python main.py --api-only
    python main.py --webdav-only
"""

import argparse
import logging
import threading
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nexanote")


def run_webdav(host, port, data_dir, username, password):
    from nexanote.sync.server import run_server
    run_server(
        host=host,
        port=port,
        data_dir=data_dir,
        username=username,
        password=password,
    )


def run_api(host, port, data_dir):
    import uvicorn
    from nexanote.storage.database import NexaNoteDB
    from nexanote.api.routes import create_app

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = NexaNoteDB(data_dir / "nexanote.db")
    app = create_app(db)

    logger.info(f"API REST démarrée sur http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(
        description="NexaNote Backend",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--webdav-port", type=int, default=8765)
    parser.add_argument("--api-port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".nexanote")
    parser.add_argument("--username", default="nexanote")
    parser.add_argument("--password", default="nexanote")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--webdav-only", action="store_true")
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════╗
║          NexaNote Backend v0.1           ║
╚══════════════════════════════════════════╝
""")

    if not args.api_only:
        print(f"  WebDAV  → http://{args.host}:{args.webdav_port}/")
    if not args.webdav_only:
        print(f"  API     → http://{args.host}:{args.api_port}/")
    print(f"  Data    → {args.data_dir}")
    print(f"  User    → {args.username}")
    print()

    threads = []

    if not args.api_only:
        t_webdav = threading.Thread(
            target=run_webdav,
            args=(args.host, args.webdav_port, args.data_dir, args.username, args.password),
            daemon=True,
            name="webdav",
        )
        threads.append(t_webdav)

    if not args.webdav_only:
        t_api = threading.Thread(
            target=run_api,
            args=(args.host, args.api_port, args.data_dir),
            daemon=True,
            name="api",
        )
        threads.append(t_api)

    for t in threads:
        t.start()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nArrêt du serveur NexaNote.")


if __name__ == "__main__":
    main()
