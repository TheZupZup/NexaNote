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
- **Local-first / offline** — install and use the app with no backend at all;
  notes are saved on-device in local SQLite. Connecting a server for WebDAV
  sync is entirely optional.

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

## Connecting the app (optional)

NexaNote is **local-first**: on first launch you can tap **"Use NexaNote
offline"** and start taking notes immediately. Notes are stored on the device
in a local SQLite database — no backend, NAS, or Docker required. You only need
the steps below if you want to **sync** across devices.

When you're ready to sync, open **Settings → Backend API** in the app and enter
your backend URL. Until then the app stays fully usable and the sync button
simply explains that sync needs a backend.

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

To cut a release (maintainers) — **entirely from the GitHub web UI, no local
checkout required**:

1. **Run the Prepare release workflow.** From the repo's
   **Actions → Prepare release → Run workflow**, enter the next `version_name`
   (semver `X.Y.Z`) and a higher `version_code` (a monotonically increasing
   integer). The workflow bumps `app/pubspec.yaml` to `X.Y.Z+versionCode`, adds
   the matching F-Droid build entry in `metadata/com.nexanote.app.yml` (with
   `commit: vX.Y.Z`), and rolls the `## Unreleased` section of `CHANGELOG.md`
   into a `## vX.Y.Z` entry — all on a `release/vX.Y.Z` branch — then opens (or
   updates) a pull request into `main`. It **never** pushes to the protected
   `main` branch and **never** creates the tag itself.

   - The run is **safe to retry**: it is idempotent on the `release/vX.Y.Z`
     branch and reuses the same PR. It stops early with a clear message only if
     the tag `vX.Y.Z` already exists.
   - The PR is opened as a **draft** — a maintainer marks it ready once the
     checks are green.
   - Tick **dry run** to validate the version/tag/branch state without
     committing or pushing anything.
   - If your repository blocks Actions from opening PRs, the run still pushes
     the branch and prints a **manual PR URL**
     (`…/compare/main...release/vX.Y.Z`) in the job summary.

2. **Merge the release PR.** Reviewing and merging the PR is what lands the
   version bump on `main`. CI runs a **release-bump guard** on `release/*` PRs
   (`scripts/check_release_bump_files.sh`) that fails if the PR touches anything
   beyond the version files (`app/pubspec.yaml`, `metadata/com.nexanote.app.yml`,
   `CHANGELOG.md`), so the release commit stays a clean, version-only diff.

3. **Create the GitHub Release / tag `vX.Y.Z` from `main`.** Use
   **Releases → Draft a new release**, choose tag `vX.Y.Z` targeting `main`, and
   publish. The tag must match the `pubspec.yaml` versionName that the merged PR
   set.

4. **GitHub Actions publishes** the artifacts automatically. The Docker workflow
   pushes `thezupzup/nexanote:X.Y.Z` and `thezupzup/nexanote:latest`; the
   Android workflow builds the release APK, titles the GitHub Release
   **NexaNote vX.Y.Z**, and attaches the stable-named `NexaNote-Android.apk`
   asset that Obtainium tracks.

Both publish workflows fail fast (with a message naming the current tag version,
the current pubspec version, and the next action) if the tag and the
`pubspec.yaml` version disagree, so the published version always matches the tag
you created.

> **Local alternative.** `scripts/bump_version.py X.Y.Z <code>` performs the
> same bump on your machine if you prefer to commit and tag manually; it does
> not create a git tag unless you pass `--tag`. Before pushing a tag, run
> `scripts/release_preflight.sh vX.Y.Z` — the same check the Android publish
> workflow runs — to confirm the tag matches `app/pubspec.yaml` and see the
> next safe commands.

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
