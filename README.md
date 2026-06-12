# ia-mirror

[![Docker Pulls](https://img.shields.io/docker/pulls/themorgantown/ia-mirror)](https://hub.docker.com/r/themorgantown/ia-mirror)

ia-mirror is a Docker-first Internet Archive mirroring utility. It wraps the `internetarchive` Python package and `ia` CLI with resumable downloads, batch queuing, structured reports, metadata caching, bandwidth throttling, and a persistent Web UI. See [what ia-mirror adds beyond `internetarchive`](#what-ia-mirror-adds-beyond-internetarchive) for a feature-by-feature comparison with the upstream tool.

Use reasonable concurrency, keep polite backoff enabled, and avoid unnecessary repeated metadata fetches. If you rely on Internet Archive heavily, consider donating at https://archive.org/donate/.

## What It Does

- Mirrors or downloads Internet Archive items with resume support
- Runs as either a Web UI queue manager or a direct CLI fetcher
- Persists job history and UI state in SQLite
- Emits per-item logs, status snapshots, and machine-readable `report.json`
- Supports dry runs, estimates, checksum verification, sync mode, glob filters, and batch CSV input

## Runtime Modes

The container starts in Web UI mode by default.

| Mode | How it starts | Primary use |
|------|---------------|-------------|
| Web UI | `WEB_ENABLED=true` or unset | Queue jobs, manage history, run downloads through the browser; no `IA_IDENTIFIER` is needed at startup |
| CLI | `WEB_ENABLED=false` | Direct one-shot downloads or scripted runs; requires `IA_IDENTIFIER` or a CLI identifier argument |

## Quick Start

Pull the latest published image:

```bash
docker pull themorgantown/ia-mirror:latest
```

### Web UI Mode (default)

```bash
docker run -d \
  --name ia-mirror \
  -v "$PWD/mirror:/downloads" \
  -v "$PWD/ia-state:/data" \
  -p 127.0.0.1:17865:17865 \
  -e WEB_HOST=0.0.0.0 \
  -e WEB_SECRET_KEY="replace-with-a-long-random-value" \
  themorgantown/ia-mirror:latest
```

Open http://localhost:17865.

Two flags deserve explanation:

- `-e WEB_HOST=0.0.0.0` is required whenever you publish the port. Inside the container, the server binds to `127.0.0.1` by default as a hardening measure, and Docker's port mapping cannot reach a loopback-bound server.
- `-p 127.0.0.1:17865:17865` keeps the UI reachable only from the machine running Docker. To reach it from other devices on your network (a NAS or homelab box, for example), use `-p 17865:17865` instead.

If `WEB_SECRET_KEY` is unset, the app generates a random secret at startup. That is safe, but sessions reset when the container restarts.

### Choosing where files are saved

The left-hand side of each `-v` flag is the folder on your computer. The example above saves into `./mirror` next to where you ran the command. To save into your Downloads folder instead:

- macOS: `-v "$HOME/Downloads/ia-mirror:/downloads"`
- Linux: `-v "$HOME/Downloads/ia-mirror:/downloads"`
- Windows: `-v "C:/Users/yourname/Downloads/ia-mirror:/downloads"`

If you use Docker Compose, set `DOWNLOAD_DIR` in a `.env` file instead — see [Docker Compose](#docker-compose).

### Windows notes

The examples in this README use bash syntax and work as-is in WSL2 and Git Bash. In PowerShell, replace the trailing `\` line continuations with backticks (`` ` ``) or put the command on one line; `$PWD` works in PowerShell, but in cmd.exe use `%cd%` instead. Write Windows paths with forward slashes, for example `C:/Users/yourname/Downloads`.

### CLI Mode

```bash
docker run --rm \
  -v "$PWD/mirror:/downloads" \
  -e WEB_ENABLED=false \
  -e IA_IDENTIFIER=The_Babe_Ruth_Collection \
  -e IA_DESTDIR=/downloads \
  -e IA_DRY_RUN=true \
  themorgantown/ia-mirror:latest
```

No credentials are needed for public-item dry runs. Remove `IA_DRY_RUN` (or set it to `0`) to actually download.

## Authentication

1. Preferred: run `ia configure` on the host, then mount `~/.config/ia:/home/app/.config/ia:ro`.
2. Fallback: provide `IA_ACCESS_KEY` and `IA_SECRET_KEY` as environment variables or save them from the Web UI Global Settings panel.
3. Production: mount an `ia.ini` secret and copy it into `/home/app/.config/ia/ia.ini` at startup.
4. Optional verification: `docker exec -it <container> ia whoami`

The entrypoint writes `/home/app/.config/ia/ia.ini` with mode `600` when credentials are supplied through environment variables.

## Ports

| Port | Variable | Purpose |
|------|----------|---------|
| `17865` | `WEB_PORT` | Web UI and API served by Gunicorn |
| `8080` | `IA_HEALTH_PORT` | Fetcher health/report server for active download jobs |

The container healthcheck targets `http://localhost:17865/api/status`.

## Volumes and Persistence

| Path | Required | Purpose |
|------|----------|---------|
| `/downloads` | Yes | Download destination, logs, reports, and status files |
| `/data` | Required by default | Web UI SQLite database and persistent queue/history state |

`/data` is only optional if you run exclusively in CLI mode with `WEB_ENABLED=false`.

When `IA_DESTDIR=/downloads`, downloaded files land in `/downloads/<identifier>/...`, with logs and status files stored alongside that item directory.

## Docker Compose

`docker-compose.yml` is intended for local Web UI use. It starts the browser UI by default and does not require an `IA_IDENTIFIER`; enter item IDs or `archive.org/details/...` URLs in the UI.

1. Start the stack: `docker compose up -d`
2. Open http://localhost:17865
3. Optional: choose a download folder — see below
4. Optional: copy the template with `cp docker/example.env docker/live.env` and edit it with credentials, Web UI defaults, or CLI-mode settings

By default the Web UI is published on `127.0.0.1` and only reachable from the machine running Docker. To allow access from other devices on your network, change the `ports:` entry in `docker-compose.yml` from `"127.0.0.1:17865:17865"` to `"17865:17865"`.

### Choosing the download folder

Without configuration, files are saved to `./downloads` next to `docker-compose.yml`. To save somewhere else, create a `.env` file at the project root (Compose reads it automatically):

```bash
cp .env.example .env
```

Then set `DOWNLOAD_DIR` to a full absolute path:

```bash
# macOS
DOWNLOAD_DIR=/Users/yourname/Downloads
# Windows
DOWNLOAD_DIR=C:/Users/yourname/Downloads
# Linux
DOWNLOAD_DIR=/home/yourname/Downloads
```

Notes:

- Tilde (`~`) is not expanded by Docker Compose — write the full path.
- Quote paths containing spaces: `DOWNLOAD_DIR="/Users/yourname/My Downloads"`.
- `DATA_DIR` works the same way for the `/data` volume (database and queue state).
- This setting must go in `.env` at the project root, not `docker/live.env`. Compose only reads volume paths from `.env`.
- Apply changes with `docker compose down && docker compose up -d`.

You can also change this from the Web UI: the Settings panel shows the current download location and, when you type a new path, generates the exact `.env` line with a copy button. A container restart applies it.

### CLI mode through Compose

Set `WEB_ENABLED=false` and a real `IA_IDENTIFIER` in `docker/live.env`, or pass them with `docker compose run --rm -e WEB_ENABLED=false -e IA_IDENTIFIER=The_Babe_Ruth_Collection ia-mirror`.

Do not use `IA_IDENTIFIER=example_item`; it is placeholder text. Blank or missing `IA_IDENTIFIER` is fine for Web UI mode, but CLI mode exits with `identifier required`.

If you already configured `ia` on your host, mount it into the service:

```yaml
services:
  ia-mirror:
    volumes:
      - ~/.config/ia:/home/app/.config/ia:ro
```

To switch from dry run to real downloads, set `IA_DRY_RUN=0` in your compose env file, or remove the line.

## Web UI Workflow

The Web UI accepts one identifier or URL per line.

Examples:

- `baberuthstory0000ruth`
- `https://archive.org/details/baberuthstory0000ruth`
- `archive.org/details/baberuthstory0000ruth`

The UI normalizes each line into an IA identifier, then enqueues jobs with the chosen operation and config.

Basic controls include:

- destination under `/downloads`
- operation type (`download`, `verify`, `sync`)
- dry run
- checksum verification
- concurrency and bandwidth cap
- glob, exclude, and format filters
- collection mode and verify-only mode

The Global Settings modal persists UI defaults and optional IA credentials to the SQLite database. It also shows the current host download location and helps you change it (see [Choosing the download folder](#choosing-the-download-folder)).

## Web UI API

### Configuration

- `GET /api/config`
- `POST /api/config`
- `GET /api/destinations`
- `POST /api/destinations/validate`
- `POST /api/maintenance/clear-history`

### Queue and job control

- `POST /api/queue/add`
- `POST /api/queue/reorder`
- `DELETE /api/queue/<id>`
- `POST /api/job/start`
- `POST /api/job/stop`
- `POST /api/jobs/<id>/unlock`

### Status and history

- `GET /api/status`
- `GET /api/jobs`
- `GET /api/jobs/recent`
- `GET /api/jobs/<id>`
- `GET /api/jobs/<id>/log`
- `GET /api/jobs/<id>/logs`

### Collection watcher

- `GET /api/watcher/collections`
- `POST /api/watcher/collections`
- `DELETE /api/watcher/collections/<identifier>`

### File browser

- `GET /api/files/list`
- `GET /api/files/download`
- `GET /api/files/content`
- `POST /api/files/delete`

The file browser is restricted to `/downloads` and rejects traversal attempts.

### WebSocket events

Clients connect to `/` and can request status with `request_status`. The server emits `status_update`, `job_update`, `job_progress`, `log_line`, and `queue_update`.

## CLI Examples

### Dry run

```bash
docker run --rm \
  -v "$PWD/mirror:/downloads" \
  -e WEB_ENABLED=false \
  -e IA_IDENTIFIER=listofearlyameri00fren \
  -e IA_DESTDIR=/downloads \
  -e IA_DRY_RUN=true \
  themorgantown/ia-mirror:latest
```

### Real download with host auth

```bash
docker run --rm \
  -v "$HOME/.config/ia:/home/app/.config/ia:ro" \
  -v "$PWD/mirror:/downloads" \
  -e WEB_ENABLED=false \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_DESTDIR=/downloads \
  -e IA_CONCURRENCY=6 \
  -e IA_CHECKSUM=1 \
  themorgantown/ia-mirror:latest
```

### Verify only

```bash
docker run --rm \
  -v "$PWD/mirror:/downloads" \
  -e WEB_ENABLED=false \
  themorgantown/ia-mirror:latest \
  The_Babe_Ruth_Collection --destdir /downloads --verify-only
```

### Batch mode

Create a CSV with `source` and `destdir` columns:

```csv
source,destdir,glob,exclude,format,concurrency,verify_mode
The_Babe_Ruth_Collection,/downloads/The_Babe_Ruth_Collection,*.mp3,,mp3,10,checksum
jillem-full-archive,/downloads/jillem-full-archive,,*_thumb.jpg,,5,size
```

Run it with:

```bash
docker run --rm \
  -v "$PWD/mirror:/downloads" \
  -v "$PWD/batch_source.csv:/app/batch_source.csv:ro" \
  -e WEB_ENABLED=false \
  -e IA_ACCESS_KEY=AKXXX \
  -e IA_SECRET_KEY=SKYYY \
  themorgantown/ia-mirror:latest \
  --use-batch-source --batch-source-path /app/batch_source.csv
```

## Key Environment Variables

See [docker/example.env](docker/example.env) for the full template.

### Docker Compose (`.env` at project root)

| Variable | Default | Notes |
|----------|---------|-------|
| `DOWNLOAD_DIR` | `./downloads` | Host folder mounted at `/downloads`; full absolute paths only |
| `DATA_DIR` | `./data` | Host folder mounted at `/data` |

### Web UI

| Variable | Default | Notes |
|----------|---------|-------|
| `WEB_ENABLED` | `true` | Starts the Web UI unless explicitly disabled |
| `WEB_HOST` | `127.0.0.1` | Gunicorn bind host inside the container. Set to `0.0.0.0` whenever you publish the port; `docker-compose.yml` does this for you |
| `WEB_PORT` | `17865` | Web UI listen port |
| `WEB_DB_PATH` | `/data/ui.db` | SQLite database path used by the entrypoint |
| `WEB_RUNNER` | `real` | `real` or `mock` |
| `WEB_SECRET_KEY` | generated at startup if unset | Set explicitly for stable sessions |
| `WEB_CORS_ORIGINS` | unset | Optional comma-separated allowed origins for separate frontends; leave unset for same-origin Web UI use |

### Fetcher / CLI

| Variable | Default | Notes |
|----------|---------|-------|
| `IA_IDENTIFIER` | none | Not needed for Web UI startup; required in CLI mode unless provided as a CLI arg |
| `IA_DESTDIR` | `/downloads` | Root destination directory |
| `IA_CONCURRENCY` | `4` | Parallel workers |
| `IA_DRY_RUN` | `false` | Simulate downloads |
| `IA_VERIFY_MODE` | `size` | `exists`, `size`, or `checksum` |
| `IA_CHECKSUM` | unset | Shortcut for checksum verification |
| `IA_SYNC` | unset | Deletes local files that no longer exist remotely |
| `IA_MAX_MBPS` | unset | Native Python throttling |
| `IA_HEALTH_PORT` | `8080` | Disable with `0` |
| `IA_USE_BATCH_SOURCE` | unset | Enables CSV batch mode |
| `IA_BATCH_SOURCE_PATH` | `./batch_source.csv` | Batch CSV path |

## Outputs

Per item, ia-mirror writes:

- `/downloads/<identifier>/ia_download.log`
- `/downloads/<identifier>/report.json`
- `/downloads/<identifier>/.ia_status/<identifier>.json`
- `/downloads/<identifier>/.ia_status/lock.json`

`report.json` is produced for dry runs and estimate-only runs as well as completed downloads.

## What ia-mirror Adds Beyond `internetarchive`

Upstream [`jjjake/internetarchive`](https://github.com/jjjake/internetarchive) provides the `ia` CLI and Python API for Archive.org operations such as download, upload, search, metadata edits, listing, copy/move/delete, reviews, tasks, and account/configuration commands. `ia-mirror` keeps `internetarchive` as its core dependency, then adds the mirroring appliance features below.

| Feature in ia-mirror | Upstream `internetarchive` status | What this project adds |
|----------------------|-----------------------------------|------------------------|
| Persistent Web UI | Not native; upstream is CLI/Python API focused | Browser queue manager, global settings, job history, file browser, log viewer, and WebSocket progress |
| Docker-first appliance | Not native; upstream supports `pip`, `pipx`, source installs, and a standalone binary | Production container with Gunicorn Web UI, healthcheck, non-root runtime, Compose/Unraid-oriented defaults, and mounted `/downloads` + `/data` state |
| SQLite job queue and history | Not native | Durable queued/running/completed job state, reorder/delete controls, and automatic queue resume after restart |
| Per-item mirror reports | Not native | `report.json`, `.ia_status/<identifier>.json`, lock files, and status snapshots beside each downloaded item |
| Built-in parallel mirror workers | Upstream recommends composing with tools such as GNU Parallel for multi-item concurrency | `-j`/`IA_CONCURRENCY` worker pool inside the wrapper with aggregate progress and ETA |
| Collection watcher | Not native | Background service that watches collections and queues new/future items |
| Browser/API batch input | Partially covered by upstream `--itemlist` and `--search` | Paste identifiers or archive.org URLs into the UI/API and normalize them into queued jobs with shared settings |
| CSV source-to-destination batch mode | Not native for downloads | Batch CSV mode that maps each source identifier to its own destination path and wrapper settings |
| Verify-only mirror checks | Not native as a standalone download workflow | Check existing local files without downloading, with `exists`, `size`, or `checksum` verification modes |
| Local sync cleanup | Not native for local mirrors | `--sync` removes local files that are no longer present in the remote IA item manifest |
| Estimate and cost reporting | Not native | `--estimate-only`, dry-run reports, assumed bandwidth, and optional cost-per-GB calculations |
| Bandwidth cap and aggregate speed sampling | Not native | Approximate `--max-mbps` throttling plus sampled aggregate transfer speed/ETA |
| Polite global backoff controls | Partially covered by upstream retry/timeout flags | Wrapper-level exponential backoff for HTTP 429/5xx responses with configurable base/max/multiplier/jitter |
| Container-friendly env configuration | Upstream has config files and its own credential/env conventions | `IA_*` and `WEB_*` env-to-argument injection, `--print-effective-config`, and automatic `ia.ini` creation from `IA_ACCESS_KEY`/`IA_SECRET_KEY` |
| ZIP folder resume helper | Not native | `--resumefolders` skips ZIP downloads when the expected extracted folder already exists |

## Development

### Build locally

```bash
docker build --pull --rm -f docker/Dockerfile -t ia-mirror:local docker
```

Multi-arch release build example:

```bash
docker buildx create --use --name ia-builder || true
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg IA_PYPI_VERSION=5.8.0 \
  --build-arg PROJECT_VERSION=$(cat VERSION) \
  -t themorgantown/ia-mirror:$(cat VERSION) --push -f docker/Dockerfile docker
```

### Useful commands

```bash
python -m py_compile docker/fetcher.py
docker run --rm ia-mirror:local --print-effective-config
./tests/runtests.sh
```

## Security and Release Readiness

- Container runs as the non-root `app` user.
- The Web UI binds to `127.0.0.1` inside the container by default; exposure is opt-in via `WEB_HOST=0.0.0.0` plus port publishing.
- Environment-provided IA credentials are written with restrictive permissions.
- The Web UI does not ship with a static default secret; an ephemeral secret is generated when `WEB_SECRET_KEY` is unset.
- Cross-origin API access is disabled by default; set `WEB_CORS_ORIGINS` only for trusted separate frontends.
- The repository runs `pip-audit`, Dockerfile linting, image builds, and SBOM generation in CI.
- Docker Scout monitoring runs on a schedule for image vulnerability review.

For local image scanning:

```bash
docker build -f docker/Dockerfile -t ia-mirror:local docker
docker scout quickview ia-mirror:local
docker scout cves ia-mirror:local
docker scout recommendations ia-mirror:local
```

## Testing

The release gate is the full test suite in [tests/runtests.sh](tests/runtests.sh). It covers Python backend tests, CLI integration tests, and Web UI integration tests.

To run everything:

```bash
./tests/runtests.sh
```

Test artifacts are written under `tests/test_output/`.

## Troubleshooting

### Container is healthy but the UI is unreachable

The server inside the container binds to `127.0.0.1` by default, which port publishing cannot reach. Pass `-e WEB_HOST=0.0.0.0` with `docker run` (the bundled `docker-compose.yml` already sets it). The healthcheck still passes in this state because it runs inside the container.

### Web UI port already in use

```bash
docker run -d \
  -v "$PWD/mirror:/downloads" \
  -v "$PWD/ia-state:/data" \
  -p 127.0.0.1:9090:17865 \
  -e WEB_HOST=0.0.0.0 \
  themorgantown/ia-mirror:latest
```

Then open http://localhost:9090.

### Database is locked

Only one container should write to the same `WEB_DB_PATH` at a time.

### Jobs are queued but not running

Check:

- `WEB_RUNNER=real`
- valid IA credentials for private content
- container logs with `docker logs <container>`

### WebSocket updates do not appear

Check browser console errors, verify that the mapped Web UI port is reachable, and try a hard refresh.

## Support

If the image cannot find `ia`, verify that the image was built with the intended `IA_PYPI_VERSION`. If you hit a bug, open an issue at https://github.com/themorgantown/ia-mirror/issues.
