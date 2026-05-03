# NexaNote

Open-source, privacy-friendly, self-hostable note-taking app with stylus support.

An alternative to Samsung Notes, OneNote and GoodNotes that respects your data.

---
## About development

This project is actively developed by me, with the help of AI tools for productivity and experimentation.
All design decisions and direction are fully human-driven.

---
## What works today

- Typed notes stored as plain Markdown files (Obsidian-style)
- Handwritten notes with stylus or mouse (pen, highlighter, eraser, pressure sensitivity)
- Notebooks to organize your notes
- WebDAV sync with your NAS, Nextcloud, or any WebDAV server
- Offline-first — works without internet
- Conflict resolution when editing the same note on multiple devices
- Search by title
- Linux desktop app (Flutter)
- Python backend with REST API and WebDAV server

## What's coming

- Android app
- PDF export
- Handwriting OCR
- Page templates (lined, grid, dotted)
- End-to-end encryption

See [docs/android-roadmap.md](docs/android-roadmap.md) for the full Android & S26 Ultra plan.

---

## Architecture

NexaNote uses two components that work together:

- **Python backend** — handles storage (file-based, Markdown), REST API, and WebDAV sync server
- **Flutter app** — the interface, runs on Linux desktop and Android (coming soon)

```
NexaNote/
├── main.py                  # Start the backend
├── requirements.txt
├── nexanote/                # Python backend
│   ├── models/              # Data models
│   ├── storage/             # File-based store (Markdown + JSON) + SQLite migration
│   ├── sync/                # WebDAV server + sync engine + conflict resolution
│   └── api/                 # REST API (FastAPI)
├── app/                     # Flutter app
│   └── lib/
│       ├── screens/         # UI screens
│       ├── widgets/         # Reusable widgets (ink canvas, notes list...)
│       └── services/        # API client, app state
└── tests/                   # 100+ tests
```

### Storage layout (v1.0.0+)

Notes are plain files on disk, Obsidian-style:

```
<data_dir>/
├── notebooks/<notebook_id>.yaml      # Notebook metadata (YAML)
├── notes/<note_id>.md                # Markdown body + YAML frontmatter
└── drawings/<note_id>.json           # Stylus strokes (one file per note)
```

Each note's frontmatter carries `title`, `tags`, `created_at`, `updated_at`,
plus the metadata the app needs (id, notebook_id, page list, sync_status).
Single-page notes have a clean body with no NexaNote-specific markers, so
they can be opened/edited directly in Obsidian or any Markdown editor.

A pre-v1.0.0 SQLite database (`nexanote.db`) is detected on first startup
and migrated automatically — the original DB is renamed to
`nexanote.db.legacy_backup` and kept in place. See
[`CHANGELOG.md`](CHANGELOG.md) for details.

---

## Getting started

### Requirements

- Python 3.10+
- Flutter 3.10+

### Backend

```bash
cd NexaNote
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

The backend starts two servers:

| Service | URL | Purpose |
|---------|-----|---------|
| REST API | http://127.0.0.1:8766 | Used by the Flutter app |
| WebDAV | http://127.0.0.1:8765 | Connect your NAS or Nextcloud |
| API docs | http://127.0.0.1:8766/docs | Interactive Swagger UI |

### Flutter app

```bash
cd NexaNote/app
flutter pub get
flutter run -d linux
```

### Launch everything at once

```bash
bash ~/NexaNote/nexanote.sh
```

This script starts the backend and the app automatically.

### Run the backend with Docker

Prefer to run the backend persistently on a NAS or always-on machine?

From source:

```bash
docker compose up -d --build
```

Or pull the prebuilt image from Docker Hub (no clone needed):

```bash
docker run -d \
  --name nexanote-backend \
  -p 8766:8766 -p 8765:8765 \
  -v /path/on/host/nexanote-data:/data \
  --restart unless-stopped \
  thezupzup/nexanote-backend:latest
```

This starts the backend on ports `8766` (API) and `8765` (WebDAV), with data
persisted in the mounted volume. The image is published as a multi-arch
manifest (`linux/amd64` + `linux/arm64`), so x86 servers and ARM NAS units
(Ugreen DXP, Raspberry Pi, etc.) both pull the right variant automatically.

See [docs/docker.md](docs/docker.md) for the full guide, including a NAS
(Synology / Ugreen) compose example and multi-arch buildx instructions.

---

## Sync with your NAS

Once your NAS has WebDAV enabled, open Settings in the app and enter your NAS URL and credentials. NexaNote will sync your notes automatically.

Tested with Ugreen NAS (UGOS Pro). Should work with any WebDAV-compatible server including Nextcloud.

---

## Running tests

```bash
python -m pytest tests/ -v
```

100+ tests covering models, file-based storage, SQLite → file migration, WebDAV provider, conflict resolution, and REST API.

---

## Contributing

The project is in early development. Contributions are welcome.

| Task | Difficulty |
|------|-----------|
| Android app (Flutter) | Hard |
| PDF export | Medium |
| Page templates | Easy |
| Handwriting OCR | Hard |
| End-to-end encryption | Hard |

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Open a pull request

---

## License

[MPL 2.0](https://www.mozilla.org/en-US/MPL/2.0/) — modifications must remain open-source, commercial use is allowed.
