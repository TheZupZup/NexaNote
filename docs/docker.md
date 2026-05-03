# Running the NexaNote backend with Docker

This guide explains how to run the NexaNote Python backend in Docker, so it can
stay running on a NAS or any always-on machine. It is written for beginners —
if you can copy/paste a command into a terminal, you can follow it.

The backend exposes:

- **API REST** on port `8766` — used by the Flutter app
- **WebDAV** on port `8765` — used for sync via Nextcloud / NAS / rclone

Data is stored in a single directory inside the container (`/data`) which is
mounted as a Docker volume so it survives container restarts and updates.

---

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (>= 20.10)
- [Docker Compose](https://docs.docker.com/compose/) (v2, included in recent
  Docker Desktop / Docker Engine releases)

You do **not** need Python or Flutter installed on the host.

---

## Quick start (from source)

From the project root:

```bash
docker compose up -d --build
```

This will:

1. Build the backend image from the `Dockerfile`.
2. Start the `nexanote-backend` service in the background.
3. Create a local `./data` folder that holds your notes (`notes/<id>.md`),
   drawings (`drawings/<id>.json`), notebooks (`notebooks/<id>.yaml`), and
   sync configuration. From v1.0.0 onwards storage is file-based; if a
   pre-v1 SQLite database exists in the volume it is migrated automatically
   on first startup and renamed to `nexanote.db.legacy_backup`.

## Quick start (from Docker Hub)

If you don't want to clone the repo, just pull the prebuilt image:

```bash
docker pull thezupzup/nexanote-backend:latest

docker run -d \
  --name nexanote-backend \
  -p 8766:8766 \
  -p 8765:8765 \
  -v /path/on/host/nexanote-data:/data \
  --restart unless-stopped \
  thezupzup/nexanote-backend:latest
```

Replace `/path/on/host/nexanote-data` with the directory where you want your
notes to live on the host.

Check it is running:

```bash
docker compose ps
```

---

## Build

To (re)build the image without starting it:

```bash
docker compose build
```

Or build the image directly with `docker build`:

```bash
docker build -t thezupzup/nexanote-backend:latest .
```

You can also tag a specific version alongside `latest`:

```bash
docker build \
  -t thezupzup/nexanote-backend:latest \
  -t thezupzup/nexanote-backend:1.0.0 \
  .
```

A plain `docker build` produces an image for the host architecture only. If
you publish from an x86 laptop and pull the image onto an ARM NAS (such as a
Ugreen DXP, Raspberry Pi, or Apple Silicon dev machine), Docker will warn
about a platform mismatch and may refuse to run. The next section covers how
to publish a single tag that supports both `linux/amd64` and `linux/arm64`.

---

## Publish to Docker Hub (multi-arch)

Maintainers only — most users do not need this.

NexaNote backend images are published as multi-arch manifests so the same
tag (`latest`, `0.1.0`, …) works on:

- `linux/amd64` — typical x86_64 servers, Synology Plus, Intel/AMD NAS
- `linux/arm64` — Ugreen DXP (ARM), Raspberry Pi 4/5, Apple Silicon, AWS Graviton

### One-time setup

You only need to do this once per machine. It enables QEMU emulation so an
x86 host can cross-build ARM images, and creates a buildx builder that
supports multi-platform output.

```bash
# Enable QEMU for foreign architectures (amd64 ↔ arm64)
docker run --privileged --rm tonistiigi/binfmt --install all

# Create and use a multi-arch builder (only needed once)
docker buildx create --name nexanote-builder --use
docker buildx inspect --bootstrap
```

Verify both platforms appear under `Platforms:`:

```
Platforms: linux/amd64, linux/arm64, ...
```

### Build and push

Log in, then build for both architectures and push in a single step. `buildx`
cannot load a multi-arch image into the local Docker daemon, so `--push` is
required (or `--output type=oci` for offline use).

```bash
docker login

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t thezupzup/nexanote-backend:latest \
  --push .
```

To cut a versioned release, push both `latest` and the version tag together
so the manifest is built once and tagged twice:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t thezupzup/nexanote-backend:latest \
  -t thezupzup/nexanote-backend:1.0.0 \
  --push .
```

The image name follows the convention `thezupzup/nexanote-backend:<tag>`.
Use `latest` for the rolling release and `MAJOR.MINOR.PATCH` for pinned
versions.

### Verify the multi-arch manifest

After pushing, confirm both architectures are present:

```bash
docker buildx imagetools inspect thezupzup/nexanote-backend:latest
```

You should see two entries under `Manifests:` — one for `linux/amd64` and
one for `linux/arm64`.

### Using Podman instead of Docker

Podman supports the same workflow with a near-identical CLI:

```bash
podman login docker.io

podman buildx build \
  --platform linux/amd64,linux/arm64 \
  -t docker.io/thezupzup/nexanote-backend:latest \
  --push .
```

If `podman buildx` is not available on your system, the equivalent
`podman manifest` flow is:

```bash
podman manifest create thezupzup/nexanote-backend:latest

podman build --platform linux/amd64 --manifest thezupzup/nexanote-backend:latest .
podman build --platform linux/arm64 --manifest thezupzup/nexanote-backend:latest .

podman manifest push --all thezupzup/nexanote-backend:latest \
  docker://docker.io/thezupzup/nexanote-backend:latest
```

### Automated releases (GitHub Actions)

Maintainers can also push a git tag matching `v*` (e.g. `v0.1.0`) to trigger
the [`docker-publish.yml`](../.github/workflows/docker-publish.yml) workflow,
which builds and pushes both architectures to Docker Hub automatically. The
workflow needs two repository secrets:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN` — a Docker Hub [access token](https://hub.docker.com/settings/security)

It tags the image with the version (`1.0.0`), the major.minor (`1.0`), and
`latest`.

---

## Start

```bash
docker compose up -d
```

The container restarts automatically (`restart: unless-stopped`), so it will
come back up after a reboot of the NAS.

---

## Stop

```bash
docker compose down
```

Your data in `./data` is **not** deleted by `down`.

---

## View logs

```bash
docker compose logs -f nexanote-backend
```

Press `Ctrl+C` to stop following.

---

## Update

When you pull a new version of the code:

```bash
git pull
docker compose up -d --build
```

Compose will rebuild the image and restart the container. The `./data` volume
keeps your notes and configuration.

---

## Running on a NAS (Synology, Ugreen, etc.)

Most NAS systems have a Docker / Container Manager UI that can pull an image
from Docker Hub and run it from a `docker-compose.yml`. NexaNote works the
same way as Plex or Jellyfin — pull the image, mount a data folder, expose the
ports.

The published image is multi-arch (`linux/amd64` + `linux/arm64`), so both
Intel/AMD NAS units and ARM units (Ugreen DXP, Raspberry Pi, etc.) pull the
correct variant automatically with no platform warnings.

Example `docker-compose.yml` to drop into your NAS:

```yaml
services:
  nexanote-backend:
    image: thezupzup/nexanote-backend:latest
    container_name: nexanote-backend
    ports:
      - "8766:8766"
      - "8765:8765"
    volumes:
      - /volume2/docker/nexanote/data:/data
    restart: unless-stopped
```

Adjust the host path (`/volume2/docker/nexanote/data` above) to match where
your NAS stores Docker app data:

- **Synology**: typically `/volume1/docker/nexanote/data`
- **Ugreen (UGOS Pro)**: typically `/volume2/docker/nexanote/data`
- **TrueNAS / generic Linux**: any directory you control

Then start it:

```bash
docker compose up -d
```

To override the WebDAV credentials, add an `environment:` block (see
[Configuration](#configuration) below).

---

## Connecting the Flutter app

In the Flutter app's settings, enter the API URL of your server:

```
http://<host-ip>:8766
```

Replace `<host-ip>` with the LAN IP of the NAS or machine running Docker
(for example `http://192.168.1.42:8766`). If the app is running on the same
machine as Docker, `http://localhost:8766` also works.

The WebDAV endpoint, if you need it for sync tools, is:

```
http://<host-ip>:8765/
```

---

## Configuration

The compose file exposes these environment variables (all optional):

| Variable                | Default     | Description                        |
| ----------------------- | ----------- | ---------------------------------- |
| `NEXANOTE_HOST`         | `0.0.0.0`   | Bind address inside the container  |
| `NEXANOTE_API_PORT`     | `8766`      | API REST port                      |
| `NEXANOTE_WEBDAV_PORT`  | `8765`      | WebDAV port                        |
| `NEXANOTE_DATA_DIR`     | `/data`     | Data directory inside the container|
| `NEXANOTE_USERNAME`     | `nexanote`  | WebDAV username                    |
| `NEXANOTE_PASSWORD`     | `nexanote`  | WebDAV password                    |

To change the WebDAV credentials, create a local `.env` file next to
`docker-compose.yml`:

```
NEXANOTE_USERNAME=myuser
NEXANOTE_PASSWORD=a-strong-password
```

Then restart:

```bash
docker compose up -d
```

---

## Avoiding committing secrets

- **Never** put real passwords in `docker-compose.yml`. Use a `.env` file
  instead — Compose reads it automatically.
- The repository's `.gitignore` should keep `.env` out of git. Double-check
  with `git status` before committing.
- The `./data` folder contains your notes (markdown files), drawings, and
  notebooks — do not commit it.

---

## Troubleshooting

- **Port already in use** — another service is using `8765` or `8766`. Either
  stop that service or change the host-side port in `docker-compose.yml`
  (e.g. `"9766:8766"`), then point the Flutter app to the new port.
- **App can't reach the backend** — make sure the NAS firewall allows inbound
  connections on ports `8765` / `8766`, and that you used the LAN IP, not
  `127.0.0.1`, from another device.
- **Permission errors on `./data`** — on Linux NAS systems, ensure the
  directory is writable by the user running Docker.
