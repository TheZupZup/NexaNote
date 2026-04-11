# NexaNote 📝

**Open-source, privacy-friendly, self-hostable note-taking app.**

> The free alternative to Samsung Notes, OneNote and GoodNotes.  
> Your notes belong to you — for real.

---

## Why NexaNote?

| App | Open-source | Self-hostable | Linux | Stylus | Free Sync |
|-----|:-----------:|:-------------:|:-----:|:------:|:---------:|
| Samsung Notes | ❌ | ❌ | ❌ | ✅ | ❌ |
| OneNote | ❌ | ❌ | ❌ | ✅ | ❌ |
| GoodNotes | ❌ | ❌ | ❌ | ✅ | ❌ |
| **NexaNote** | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## Features

- 🖊️ **Handwritten notes** — stylus support with pressure, eraser, highlighter
- ⌨️ **Typed notes** — text/markdown editor
- 📓 **Notebooks & pages** — organize notes into notebooks
- 🔁 **WebDAV sync** — compatible with Nextcloud, NAS, rclone, Cyberduck
- ⚡ **Offline-first** — works without internet connection
- 🔀 **Conflict resolution** — smart merging of handwritten notes across devices
- 🔍 **Search** — by title, tags, notebook
- 🏷️ **Tags** — flexible organization
- 🗑️ **Trash** — soft delete with restore
- 📤 **PDF export** — *(coming soon)*

---

## Project Architecture

NexaNote is a **polyglot project** — each part uses the best language for its purpose:

```
NexaNote/
├── 🐍 Python Backend     ← this repo  (WebDAV sync + REST API)
├── 🎯 Flutter App        ← coming soon  (Android, Linux, Windows, iOS)
└── 🗄️ SQLite             ← stdlib       (local storage)
```

---

## Getting Started

### Requirements
- Python 3.10 or newer
- pip

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USER/NexaNote.git
cd NexaNote

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate        # Linux / Mac
# venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the server
python main.py
```

### Output

```
╔══════════════════════════════════════════╗
║          NexaNote Backend v0.1           ║
╚══════════════════════════════════════════╝

  WebDAV  → http://127.0.0.1:8765/
  API     → http://127.0.0.1:8766/
  Data    → ~/.nexanote
  User    → nexanote
```

| Service | URL | Purpose |
|---------|-----|---------|
| WebDAV | `http://localhost:8765/` | Connect Nextcloud, NAS, rclone |
| REST API | `http://localhost:8766/` | Used by the Flutter app |
| API Docs | `http://localhost:8766/docs` | Interactive Swagger UI |

### Options

```bash
# Accessible from local network (phone, tablet)
python main.py --host 0.0.0.0

# Custom ports and data directory
python main.py --host 0.0.0.0 \
               --webdav-port 8765 \
               --api-port 8766 \
               --data-dir ~/my-notes \
               --username me \
               --password mysecretpassword

# WebDAV server only
python main.py --webdav-only

# REST API only
python main.py --api-only
```

---

## Tests

```bash
python -m pytest tests/ -v
```

**64 tests** covering data models, SQLite storage, WebDAV server, conflict resolution, and the full REST API.

---

## Code Structure

```
NexaNote/
├── main.py                        # Entry point — starts WebDAV + API
├── requirements.txt
├── nexanote/
│   ├── models/
│   │   └── note.py                # Note, Notebook, Page, InkStroke, Point
│   ├── storage/
│   │   └── database.py            # SQLite — full CRUD
│   ├── sync/
│   │   ├── webdav_provider.py     # Exposes notes as WebDAV resources
│   │   ├── server.py              # WebDAV server (WsgiDAV + Cheroot)
│   │   ├── conflict.py            # Conflict resolution (3 strategies)
│   │   └── client.py              # Sync engine (pull → diff → resolve → push)
│   └── api/
│       └── routes.py              # FastAPI REST routes (20+)
└── tests/
    ├── test_models_and_db.py
    ├── test_webdav_and_conflict.py
    └── test_api.py
```

---

## File Format

Notes are stored as a readable WebDAV tree — no proprietary format:

```
/
└── my-notebook__a1b2c3d4/
    └── my-note__e5f6g7h8/
        ├── note.json        ← metadata + text content (Markdown)
        ├── page_1.ink       ← handwritten strokes (JSON)
        └── page_2.ink
```

Open format — readable without NexaNote. No vendor lock-in.

---

## Contributing

The project is actively being built. **Contributions are very welcome!**

### Where you can help

| Task | Difficulty | Description |
|------|:----------:|-------------|
| Flutter app — Android | 🔴 Hard | UI, ink canvas, navigation |
| Flutter app — Linux | 🔴 Hard | Same codebase as Android |
| PDF export | 🟡 Medium | Convert notes to PDF |
| Markdown import | 🟢 Easy | Import .md files into notes |
| Nextcloud sync | 🟡 Medium | Dedicated Nextcloud backend |
| Handwriting OCR | 🔴 Hard | Search inside handwritten notes |
| Docker / docker-compose | 🟢 Easy | Easy self-hosting deployment |
| Page templates | 🟢 Easy | Lined, grid, dotted pages |
| Note encryption | 🔴 Hard | End-to-end encryption |

### How to contribute

1. Fork the repo
2. Create a branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'feat: add X'`)
4. Push (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## Roadmap

- [x] **Phase 0** — Data models + SQLite storage
- [x] **Phase 1** — WebDAV server + conflict resolution
- [x] **Phase 2** — Full REST API
- [ ] **Phase 3** — Flutter app Android + Linux (MVP)
- [ ] **Phase 4** — Nextcloud sync + PDF export
- [ ] **Phase 5** — OCR, encryption, page templates

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| REST API | FastAPI + Uvicorn |
| WebDAV Server | WsgiDAV + Cheroot |
| Database | SQLite (Python stdlib) |
| Validation | Pydantic v2 |
| Tests | Pytest |
| Mobile/Desktop app | Flutter *(coming soon)* |
| Backend language | Python 3.10+ |

---

## License

[MPL 2.0](https://www.mozilla.org/en-US/MPL/2.0/) — Modifications must remain open-source. Commercial use is allowed.

---

*Started in 2026 — contributions welcome* 🙌

