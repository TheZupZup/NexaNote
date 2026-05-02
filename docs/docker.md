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

## Quick start

From the project root:

```bash
docker compose up -d --build
```

This will:

1. Build the backend image from the `Dockerfile`.
2. Start the `nexanote-backend` service in the background.
3. Create a local `./data` folder that holds the SQLite database, notes, and
   sync configuration.

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
