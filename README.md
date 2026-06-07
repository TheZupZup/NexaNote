# NexaNote

**Self-hosted handwritten notes for your own NAS.**

NexaNote is a privacy-focused note-taking app for people who want to keep their
notes on hardware they control. It pairs a **Flutter** client (Linux desktop and
Android) with a small, self-hosted **Python (FastAPI)** backend that stores
everything as plain files on your own server or NAS.

Your notes never touch a third-party cloud. You own the storage, the sync
server, and the data.

---

## Features

- **Markdown note storage** — typed notes are saved as plain `.md` files you can
  open in Obsidian or any text editor.
- **Stylus & drawing support** — handwritten notes with pen, highlighter,
  eraser, and pressure sensitivity (mouse works too).
- **Self-hosted backend** — runs on your NAS or any always-on machine.
- **WebDAV sync** — sync notes between devices through your own WebDAV server.
- **Docker deployment** — a single multi-arch image for `amd64` and `arm64`.
- **Android APK** — installable builds published via GitHub Releases.
- **Offline-first direction** — the app is built to keep working without a
  network connection and sync when one is available.

---

## Current status

NexaNote is under **active development**. It's already usable for everyday
testing, but please set expectations accordingly:

- Core features (notes, drawings, notebooks, WebDAV sync) work today.
- Sync reliability is improving but is not yet bulletproof.
- **Not yet recommended for critical notes without your own backups.** Keep a
  copy of anything you can't afford to lose.

If you hit problems, the in-app diagnostics and sync logs (see below) make it
much easier to report what went wrong.

---

## Quick start: backend with Docker

The easiest way to run the backend is the prebuilt Docker image
`thezupzup/nexanote:latest`. Create a `docker-compose.yml`:

```yaml
services:
  nexanote-backend:
    image: thezupzup/nexanote:latest
    container_name: nexanote-backend
    ports:
      - "8766:8766"   # REST API (used by the app)
      - "8765:8765"   # WebDAV (used for sync)
    volumes:
      - ./data:/data
    restart: unless-stopped
```

Then start it:

```bash
docker compose up -d
```

The image is published as a multi-arch manifest (`linux/amd64` +
`linux/arm64`), so x86 servers and ARM NAS units pull the right variant
automatically.

> Want a single hostname / one TLS certificate instead of two ports? See
> [docs/docker.md](docs/docker.md) for reverse-proxy examples (Nginx, Caddy,
> Cloudflare Tunnel).

---

## Using a NAS

On a NAS, point the volume at a path on your storage pool instead of `./data`.
Common examples:

```yaml
# Synology
volumes:
  - /volume1/docker/nexanote/data:/data

# Ugreen (or a second pool)
volumes:
  - /volume2/docker/nexanote/data:/data
```

Tested with Ugreen NAS (UGOS Pro); it should work on any system that can run
Docker. The mounted `data` directory holds all your notes, so back it up like
any other important folder.

---

## Connecting the app

Open **Settings → Backend** in the app and enter your backend URL.

**Backend (REST API) URL:**

```
http://NAS_IP:8766
https://nexanote.example.com
```

**WebDAV URL:**

```
http://NAS_IP:8765
https://webdav-nexanote.example.com
```

If you run behind a reverse proxy that serves both on one hostname, the app can
derive the WebDAV URL automatically — see [docs/docker.md](docs/docker.md).
Otherwise, set both URLs explicitly.

---

## Android APK

There is no Play Store listing — NexaNote ships its Android builds through
**GitHub Releases**. Every release attaches a stable-named asset,
**`NexaNote-Android.apk`**, so the download link and update tooling stay
predictable from one version to the next.

### Install with Obtainium (recommended)

[Obtainium](https://github.com/ImranR98/Obtainium) installs and auto-updates
apps straight from their GitHub Releases — no Play Store, no Google account, no
tracking. To follow NexaNote:

1. Install Obtainium (it's on F-Droid, or from its own GitHub Releases).
2. Tap **Add App** and fill in:
   - **Source type:** GitHub
   - **Repository / App URL:** `https://github.com/TheZupZup/NexaNote`
   - **APK asset (if asked to filter):** `NexaNote-Android.apk`
3. Add the app. Obtainium reads the newest `vX.Y.Z` release, installs
   `NexaNote-Android.apk`, and offers an update whenever a newer release **tag**
   is published.

Because the asset name never changes between releases, Obtainium matches it
automatically and tracks new versions from the release tags.

### Manual install

Prefer to do it by hand?

1. Open the [GitHub Releases](https://github.com/TheZupZup/NexaNote/releases)
   page.
2. Download the **`NexaNote-Android.apk`** asset from the latest release.
3. Open the downloaded file to install it.
4. If prompted, allow **install from unknown sources** for your browser or file
   manager.

The Android build is functional but still being polished, so expect rough edges.
The in-app **Settings → About** card shows the exact installed version.

> **F-Droid readiness in progress.** A draft F-Droid metadata file lives at
> [`metadata/com.nexanote.app.yml`](metadata/com.nexanote.app.yml) and the
> release metadata is kept F-Droid-aligned: applicationId `com.nexanote.app`,
> `MPL-2.0` license, no Google Play or proprietary dependencies, and the
> `INTERNET` permission is used only to reach *your own* backend / WebDAV
> server. NexaNote has not been submitted to fdroiddata yet — GitHub Releases
> remains the official source for the Android APK for the time being.

---

## Local development

### Requirements

- Python 3.10+
- Flutter 3.10+

### Backend

```bash
pip install -r requirements.txt
python main.py
```

This starts two servers:

| Service  | URL                          | Purpose                      |
|----------|------------------------------|------------------------------|
| REST API | http://127.0.0.1:8766        | Used by the Flutter app      |
| WebDAV   | http://127.0.0.1:8765        | Sync                         |
| API docs | http://127.0.0.1:8766/docs   | Interactive Swagger UI       |

### Flutter app

```bash
cd app
flutter pub get
flutter run -d linux
```

### Tests

```bash
python -m pytest tests/ -v
```

---

## Release process

NexaNote uses a single **`vX.Y.Z`** git tag to drive every published version.
The Android `versionName`/`versionCode`, the Flutter `pubspec.yaml`, the GitHub
Release title, the APK asset, the Docker image tags, and the F-Droid metadata
all follow that one tag — there is no version string hardcoded in more than one
place.

To cut a release (maintainers):

1. **Bump the version.** Choose the next `versionName` (semver `X.Y.Z`) and a
   higher `versionCode` (a monotonically increasing integer):

   ```bash
   python scripts/bump_version.py 1.0.2 3
   ```

   This rewrites `app/pubspec.yaml` to `1.0.2+3`, adds the matching F-Droid
   build entry in `metadata/com.nexanote.app.yml` (with `commit: v1.0.2`), and
   rolls the `## Unreleased` section of `CHANGELOG.md` into a `## v1.0.2` entry.
   It does **not** create a git tag.

2. **Commit** the bump:

   ```bash
   git commit -am "release: NexaNote v1.0.2"
   ```

3. **Tag** the release `vX.Y.Z` (the tag must match the `pubspec.yaml`
   versionName):

   ```bash
   git tag v1.0.2
   ```

4. **Push** the commit and the tag:

   ```bash
   git push && git push origin v1.0.2
   ```

5. **GitHub Actions publishes** the artifacts. The Docker workflow pushes
   `thezupzup/nexanote:1.0.2` and `thezupzup/nexanote:latest`; the Android
   workflow builds the release APK, titles the GitHub Release
   **NexaNote v1.0.2**, and attaches the stable-named `NexaNote-Android.apk`
   asset that Obtainium tracks.

Both workflows fail fast if the tag and the `pubspec.yaml` version disagree, so
the published version always matches the tag you pushed.

---

## Storage layout

Everything lives under your `data` directory as plain files:

```
data/
├── notes/          # Markdown note bodies (+ YAML frontmatter)
├── notebooks/      # Notebook metadata (YAML)
├── drawings/       # Stylus strokes (JSON, one file per note)
└── sync_logs/      # Latest sync report (for diagnostics)
```

Because notes are plain Markdown on disk, you can read or edit them with any
Markdown editor, and back them up with ordinary file tools.

---

## Sync diagnostics

When sync misbehaves, NexaNote gives you a few ways to see what happened:

- **Sync logs** — the most recent sync writes a report to
  `data/sync_logs/latest.json` (note ids/titles and outcomes), also available
  from the backend API.
- **Dry-run** — trigger a sync with `?dry_run=true` to preview what *would*
  change without writing anything.
- **Copy diagnostics** — the app's connection screen has a button that builds a
  copy/paste-able diagnostic summary (with credentials redacted), handy for bug
  reports.

---

## Roadmap

- Stronger sync reliability
- Better Android polish
- A clearer conflict-resolution UI
- F-Droid readiness
- Optional desktop packaging

---

## Contributing

Contributions are welcome.

- Open an issue to report bugs or discuss ideas before large changes.
- Keep pull requests small and focused — one change per PR.
- Don't modify unrelated files.
- Tests are appreciated.

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch and PR conventions.

---

## License

[MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/) — modifications must remain
open-source, and commercial use is allowed. See [LICENSE](LICENSE).
