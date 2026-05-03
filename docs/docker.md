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
3. Create a local `./data` folder that holds the SQLite database, notes, and
   sync configuration.

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
  -t thezupzup/nexanote-backend:0.1.0 \
  .
```

---

## Publish to Docker Hub

Maintainers only — most users do not need this.

```bash
docker login

docker push thezupzup/nexanote-backend:latest
# and any version tags you built
docker push thezupzup/nexanote-backend:0.1.0
```

The image name follows the convention `thezupzup/nexanote-backend:<tag>`.
Use `latest` for the rolling release and `MAJOR.MINOR.PATCH` for pinned
versions.

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
- The `./data` folder contains your notes and database — do not commit it.

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
