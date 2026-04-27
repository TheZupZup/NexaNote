## 🛡️ License & Commercial Use

Nova is a project created and owned by TheZupZup.

To protect the work behind this project while keeping it accessible, Nova is distributed under a **non-commercial license**.

### TL;DR

* ✅ Personal and non-profit use is allowed
* ❌ Commercial use, resale, or SaaS hosting is not allowed without permission

For full license terms, see the [LICENSE](./LICENSE) file.

---

If you are interested in using Nova in a commercial context or want to discuss licensing:

📩 [Contact me for commercial licensing](mailto:copyright.crewmate858@passmail.net)


# NexaNote

Open-source, privacy-friendly, self-hostable note-taking app with stylus support.

An alternative to Samsung Notes, OneNote and GoodNotes that respects your data.

---
## About development

This project is actively developed by me, with the help of AI tools for productivity and experimentation.
All design decisions and direction are fully human-driven.

---
## What works today

- Typed notes with Markdown formatting
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
- Docker deployment

---

## Architecture

NexaNote uses two components that work together:

- **Python backend** — handles storage (SQLite), REST API, and WebDAV sync server
- **Flutter app** — the interface, runs on Linux desktop and Android (coming soon)

```
NexaNote/
├── main.py                  # Start the backend
├── requirements.txt
├── nexanote/                # Python backend
│   ├── models/              # Data models
│   ├── storage/             # SQLite layer
│   ├── sync/                # WebDAV server + sync engine + conflict resolution
│   └── api/                 # REST API (FastAPI)
├── app/                     # Flutter app
│   └── lib/
│       ├── screens/         # UI screens
│       ├── widgets/         # Reusable widgets (ink canvas, notes list...)
│       └── services/        # API client, app state
└── tests/                   # 64 tests
```

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

---

## Sync with your NAS

Once your NAS has WebDAV enabled, open Settings in the app and enter your NAS URL and credentials. NexaNote will sync your notes automatically.

Tested with Ugreen NAS (UGOS Pro). Should work with any WebDAV-compatible server including Nextcloud.

---

## Running tests

```bash
python -m pytest tests/ -v
```

64 tests covering models, database, WebDAV provider, conflict resolution, and REST API.

---

## Contributing

The project is in early development. Contributions are welcome.

| Task | Difficulty |
|------|-----------|
| Android app (Flutter) | Hard |
| PDF export | Medium |
| Docker / docker-compose | Easy |
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
