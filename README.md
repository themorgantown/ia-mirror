# ia-mirror (Dockerized Internet Archive mirror utility)

[![Docker Pulls](https://img.shields.io/docker/pulls/themorgantown/ia-mirror)](https://hub.docker.com/r/themorgantown/ia-mirror)  

Minimal Dockerized MVP for mirroring/downloading items from the Internet Archive using the `ia` CLI (via the `internetarchive` Python package).

This repository provides a small container that wraps parallel downloads, resume, dry-run, checksum verification and a small summary `report.json` written alongside the downloaded data. Useful for large, resumed, or complex downloads where dockerization makes life easier.
New: multi-glob includes, exclude filters, optional format restriction, lockfile safety (default-on), and structured reports for `--dry-run` and `--estimate-only`.
Optional: batch mode via a simple `batch_source.csv` to run multiple mirrors in one go.

## QuickStart:

Pull the latest published image (recommended):

```bash
docker pull themorgantown/ia-mirror:latest
```

```
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=The_Babe_Ruth_Collection \
  -e IA_DESTDIR=/data \
  -e IA_ACCESS_KEY=your_access_key_here \
  -e IA_SECRET_KEY=your_secret_key_here \
  themorgantown/ia-mirror:latest
```

1. Obtain archive.org credentials:
  - Create an account (https://archive.org/account/login).
  - Generate or locate your access key and secret at https://archive.org/account/s3.php.

2. (Host config method) On your host run:
  ia configure
  This creates ~/.config/ia/ia.ini. Then run the container mounting it read-only:
  -v "$HOME/.config/ia:/home/app/.config/ia:ro"

3. (Env var method – recommended for ephemeral creds) Supply:
  -e IA_ACCESS_KEY=XXXX -e IA_SECRET_KEY=YYYY
  The entrypoint writes /home/app/.config/ia/ia.ini at runtime.

4. (Docker secrets) Store ia.ini contents (or just key/secret lines) in a secret, mount it (e.g. /run/secrets/ia.ini), then copy or cat it to /home/app/.config/ia/ia.ini via a small wrapper script or custom entrypoint.

5. Verify inside a running container (optional):
  docker exec -it <container> ia whoami

No credentials needed for public-item dry runs (use --dry-run).  

## Docker Compose (Recommended for Local Development)

A `docker-compose.yml` is provided for easy local testing with sane defaults. It runs in dry-run mode by default to show what would be downloaded without actually fetching files.

1. Edit `docker-compose.yml` and replace `example_item` with your actual IA identifier.
2. Run: `docker-compose up`
3. This will perform a dry-run download, showing estimates and what files would be fetched.

To switch to a real download:
- Edit `docker-compose.yml` and change `IA_DRY_RUN=1` to `IA_DRY_RUN=0` (or remove the line).
- Or run: `docker-compose run --rm ia-mirror env IA_DRY_RUN=0`

Add credentials if needed (see QuickStart above).

## Examples:

docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" themorgantown/ia-mirror:latest

### With more parallel downloads
docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" -e IA_CONCURRENCY="10" themorgantown/ia-mirror:latest

### Dry run to see what would be downloaded
docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" -e IA_DRY_RUN="true" themorgantown/ia-mirror:latest

### Verify-only (no downloads; checks local files)
docker run --rm -v $(pwd)/mirror:/data themorgantown/ia-mirror:latest The_Babe_Ruth_Collection --destdir /data --verify-only

## Quick concepts
- Downloads are stored in the container-mounted `/data` volume (by default). Status and snapshots are stored in `DEST/.ia_status` and `DEST/report.json`.
- Authentication can be provided via:
  - mounting your host `~/.config/ia` into the container (read-only) — quick but exposes host config
  - environment variables `IA_ACCESS_KEY` and `IA_SECRET_KEY` (the entrypoint will generate `~/.config/ia/ia.ini` at runtime)
  - Docker secrets (recommended) — mount or inject into env in CI

# Development

## Build (local)

Build a single-arch image (defaults to `IA_PYPI_VERSION=5.5.0`):

```bash
docker build -t themorgantown/ia-mirror:0.1.0 -f docker/Dockerfile docker
docker build --pull --rm -f docker/Dockerfile -t ia-mirror:local docker
```

Multi-arch build (recommended for publishing):

```bash
docker buildx create --use --name ia-builder || true
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg IA_PYPI_VERSION=5.5.0 \
  -t themorgantown/ia-mirror:0.1.0 --push -f docker/Dockerfile docker
```

## Usage examples

Dry-run (no credentials needed for public items):

```bash
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  themorgantown/ia-mirror:latest --dry-run
```

Run with host `ia` config (quick):

```bash
docker run --rm \
  -v "$HOME/.config/ia:/home/app/.config/ia:ro" \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_CONCURRENCY=6 \
  -e IA_CHECKSUM=1 \
  themorgantown/ia-mirror:latest
```

Run using env creds (safer than mounting whole config):

```bash
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_ACCESS_KEY=AKXXX -e IA_SECRET_KEY=SKYYY \
  -e IA_CONCURRENCY=6 \
  themorgantown/ia-mirror:latest
```

  ### Batch mode (optional)

  Create a CSV with two columns: `source` (IA identifier) and `destdir` (target path inside container):

  `batch_source.csv`

  ```
  source,destdir
  The_Babe_Ruth_Collection,/data/The_Babe_Ruth_Collection
  jillem-full-archive,/data/jillem-full-archive
  ```

  Run the container with batch mode enabled (mount the CSV and set dest roots as needed):

  ```bash
  docker run --rm \
    -v "$PWD/mirror:/data" \
    -v "$PWD/batch_source.csv:/app/batch_source.csv:ro" \
    -e IA_ACCESS_KEY=AKXXX -e IA_SECRET_KEY=SKYYY \
    themorgantown/ia-mirror:latest --use-batch-source --batch-source-path /app/batch_source.csv
  ```
  All other flags (e.g., `-g`, `-x`, `-f`, `-j`, `--checksum`) apply to every row in the CSV.

Recommended for production: use Docker secrets or your orchestration's secret mechanism and inject into the container as env vars or bind a single secret file as `/run/secrets/ia.ini` then copy into `/home/app/.config/ia/ia.ini` at startup.

## Config / ENV variables
- IA_IDENTIFIER (required) — item or collection identifier
- IA_DESTDIR — destination directory under /data (container resolves path)
- IA_GLOB (-g) — include glob; can be repeated or comma-separated (default `*`)
- IA_EXCLUDE (-x) — exclude glob(s); can be repeated or comma-separated
- IA_FORMAT (-f) — restrict to extensions (e.g., `mp3,flac`)
- IA_CONCURRENCY (-j) — parallel workers (default 5)
- IA_CHECKSUM — enable checksums
- IA_DRY_RUN — dry-run
- IA_VERIFY_ONLY, IA_ESTIMATE_ONLY, IA_COLLECTION, IA_RESUMEFOLDERS
- IA_MAX_MBPS (optional) — bandwidth cap in Mbps; default off. Only applies if set (>0). If `trickle` is available in PATH it will be used; otherwise the cap is ignored with a warning.
- IA_ASSUMED_MBPS — used for ETA/estimate math when uncapped (default 100)
- IA_COST_PER_GB — include egress cost estimate
- Polite backoff (enabled by default):
  - IA_NO_BACKOFF — set truthy to disable exponential backoff
  - IA_BACKOFF_BASE, IA_BACKOFF_MAX, IA_BACKOFF_MULTIPLIER, IA_BACKOFF_JITTER — tune backoff (defaults: 2s, 60s, 2.0, 0.25)
- IA_LOG_LEVEL — INFO/DEBUG/ERROR (controls stdout/file logging)
- IA_NO_LOCK — set truthy to disable lockfile (not recommended)
- IA_USE_BATCH_SOURCE — truthy to enable batch mode
- IA_BATCH_SOURCE_PATH — path to batch CSV (default `./batch_source.csv`)
- IA_ACCESS_KEY / IA_SECRET_KEY — short-lived env-based credentials

 
## Files & outputs
- Download destination: `/data/<identifier>` (unless `--destdir` provided)
- Status dir: `/data/<identifier>/.ia_status/<identifier>.json`
- Lockfile: `/data/<identifier>/.ia_status/lock.json` (auto-removed on exit)
- Snapshot report: `/data/<identifier>/report.json`
- Log file: `/data/<identifier>/ia_download.log` (also streamed to stdout)

Note on `--destdir` layout: the underlying `ia` CLI writes files under `<destdir>/<identifier>/...`. When you set `IA_DESTDIR=/data`, this wrapper resolves the working directory to `/data/<identifier>` for logs/status, and instructs `ia` to write to `/data` so files land in `/data/<identifier>/...` (no double-nesting). Using the examples above will produce the expected layout.

Report behavior:
- `--dry-run` now writes a structured `report.json` summarizing totals and simulated ETA.
- `--estimate-only` writes a structured `report.json` with known/remaining bytes and estimated seconds, then exits without downloading.

## Troubleshooting
- If the image cannot find `ia`, ensure the `internetarchive` package version installed in the image provides the `ia` CLI (we pin with `IA_PYPI_VERSION` build arg). You can also bind a local `ia` binary into `/app/ia`.
