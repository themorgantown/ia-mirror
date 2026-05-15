# ia-mirror

[![Docker Pulls](https://img.shields.io/docker/pulls/themorgantown/ia-mirror)](https://hub.docker.com/r/themorgantown/ia-mirror)

ia-mirror is a Docker-first Internet Archive mirroring utility. It wraps the `internetarchive` Python package and `ia` CLI with resumable downloads, batch queuing, structured reports, metadata caching, bandwidth throttling, and a persistent Web UI.

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
| Web UI | `WEB_ENABLED=true` or unset | Queue jobs, manage history, run downloads through the browser |
| CLI | `WEB_ENABLED=false` | Direct one-shot downloads or scripted runs |

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
  -p 17865:17865 \
  -e WEB_SECRET_KEY="replace-with-a-long-random-value" \
  themorgantown/ia-mirror:latest
```

Open http://localhost:17865.

If `WEB_SECRET_KEY` is unset, the app now generates a random secret at startup. That is safe, but sessions will reset when the container restarts.

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

No credentials are needed for public-item dry runs.

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

`docker-compose.yml` is intended for local Web UI development and defaults to dry-run behavior.

1. Copy the template: `cp docker/example.env docker/live.env`
2. Edit `docker/live.env` with your credentials and settings
3. Replace `example_item` in `docker-compose.yml` if needed
4. Start the stack: `docker compose up -d`
5. Open http://localhost:17865

If you already configured `ia` on your host, mount it into the service:

```yaml
services:
  ia-mirror:
    volumes:
      - ~/.config/ia:/home/app/.config/ia:ro
```

To switch from dry run to real downloads, set `IA_DRY_RUN=0` in your compose env file.

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

The Global Settings modal persists UI defaults and optional IA credentials to the SQLite database.

## Web UI API

### Configuration

- `GET /api/config`
- `POST /api/config`
- `GET /api/destinations`
- `POST /api/destinations/validate`

### Queue and job control

- `POST /api/queue/add`
- `POST /api/queue/reorder`
- `DELETE /api/queue/<id>`
- `POST /api/job/start`
- `POST /api/job/stop`

### Status and history

- `GET /api/status`
- `GET /api/jobs`
- `GET /api/jobs/<id>`
- `GET /api/jobs/<id>/log`

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

### Web UI

| Variable | Default | Notes |
|----------|---------|-------|
| `WEB_ENABLED` | `true` | Starts the Web UI unless explicitly disabled |
| `WEB_HOST` | `0.0.0.0` | Gunicorn bind host |
| `WEB_PORT` | `17865` | Web UI listen port |
| `WEB_DB_PATH` | `/data/ui.db` | SQLite database path used by the entrypoint |
| `WEB_RUNNER` | `real` | `real` or `mock` |
| `WEB_SECRET_KEY` | generated at startup if unset | Set explicitly for stable sessions |

### Fetcher / CLI

| Variable | Default | Notes |
|----------|---------|-------|
| `IA_IDENTIFIER` | none | Required unless provided as a CLI arg |
| `IA_DESTDIR` | `/downloads` | Root destination directory |
| `IA_CONCURRENCY` | `5` | Parallel workers |
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
- Environment-provided IA credentials are written with restrictive permissions.
- The Web UI no longer ships with a static default secret; an ephemeral secret is generated when `WEB_SECRET_KEY` is unset.
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

The existing release gate is the full test suite in [tests/runtests.sh](tests/runtests.sh). It covers Python backend tests, CLI integration tests, and Web UI integration tests.

To run everything:

```bash
./tests/runtests.sh
```

Test artifacts are written under `tests/test_output/`.

## Troubleshooting

### Web UI port already in use

```bash
docker run -d \
  -v "$PWD/mirror:/downloads" \
  -v "$PWD/ia-state:/data" \
  -p 9090:17865 \
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
