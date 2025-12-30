# ia-mirror (Dockerized Internet Archive mirror utility)

[![Docker Pulls](https://img.shields.io/docker/pulls/themorgantown/ia-mirror)](https://hub.docker.com/r/themorgantown/ia-mirror)  

Minimal Dockerized MVP for mirroring/downloading items from the Internet Archive using the `ia` CLI (via the `internetarchive` Python package).

This repository is great for large-scale, resumed, or complex downloads. It wraps the official Internet Archive Python library with advanced features like parallel downloads, bandwidth throttling, metadata caching, and a real-time health check server. It supports all major features of the official API plus powerful extras: multi-glob includes, exclude filters, smart resuming without redownloading metadata, format restrictions, lockfile safety, and structured JSON reports for dry-runs and estimates. Bonus: use `batch_source.csv` to run multiple mirrors in one go.

Don't abuse the IA servers. Use reasonable concurrency, enable polite backoff (default), and consider caching metadata locally to reduce API pressure.
IF you feel like donating to IA for bandwidth costs, consider supporting them at https://archive.org/donate/.

## QuickStart:

Pull the latest published image (recommended):

```bash
docker pull themorgantown/ia-mirror:latest
```

Locate the IA identifier you want to mirror (e.g., `The_Babe_Ruth_Collection`).
Run the container, mounting a local directory for downloads and specifying the identifier:
(First, get your IA credentials as described below.)

```
docker run --rm \
  -v "$PWD/mirror:/downloads" \
  -e IA_IDENTIFIER=The_Babe_Ruth_Collection \
  -e IA_DESTDIR=/downloads \
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

A `docker-compose.yml` is provided for easy local testing with sane defaults. It runs in dry-run mode by default to show what would be downloaded without actually fetching files. The Web UI is exposed on port 17865 by default.

1. Edit `docker-compose.yml` and replace `example_item` with your actual IA identifier.
2. (Optional) Create a `docker/live.env` file based on `docker/example.env` to manage your environment variables securely.
3. Run: `docker-compose up`
4. Open the Web UI at http://localhost:17865 to queue items and watch status.
5. This will perform a dry-run download, showing estimates and what files would be fetched.

To switch to a real download:
- Edit `docker-compose.yml` or your `.env` file and change `IA_DRY_RUN=1` to `IA_DRY_RUN=0`.
- Or run: `docker-compose run --rm ia-mirror env IA_DRY_RUN=0`

### Environment Variables (.env)

The container supports loading environment variables from a `.env` file. 
- **`docker/example.env`**: A comprehensive template containing all supported variables and their descriptions.
- **`docker/live.env`**: (Recommended) Create this file to store your actual credentials and local configuration. It is ignored by git to prevent accidental credential leaks.

#### Setup and Usage
1. **Copy the template**: `cp docker/example.env docker/live.env`
2. **Configure**: Edit `docker/live.env` with your `IA_ACCESS_KEY`, `IA_SECRET_KEY`, and other preferences.
3. **Run**: `docker-compose up` will automatically pick up these settings.

#### Key Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `IA_IDENTIFIER` | The IA item or collection ID to mirror | (Required) |
| `IA_ACCESS_KEY` | Your Internet Archive S3 Access Key | (Optional) |
| `IA_SECRET_KEY` | Your Internet Archive S3 Secret Key | (Optional) |
| `IA_DRY_RUN` | Set to `1` to simulate downloads without fetching | `1` (in compose) |
| `IA_CONCURRENCY` | Number of parallel downloads | `8` |
| `IA_DESTDIR` | Root directory for downloads inside container | `/downloads` |
| `WEB_ENABLED` | Enable the Web UI | `true` |

For a full list of variables and detailed descriptions, see [docker/example.env](docker/example.env).

Add credentials if needed (see QuickStart above).

## Examples:

docker run --rm -v $(pwd)/mirror:/downloads -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/downloads" themorgantown/ia-mirror:latest

### With more parallel downloads
docker run --rm -v $(pwd)/mirror:/downloads -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/downloads" -e IA_CONCURRENCY="10" themorgantown/ia-mirror:latest

### Dry run to see what would be downloaded
docker run --rm -v $(pwd)/mirror:/downloads -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/downloads" -e IA_DRY_RUN="true" themorgantown/ia-mirror:latest

### Verify-only (no downloads; checks local files)
docker run --rm -v $(pwd)/mirror:/downloads themorgantown/ia-mirror:latest The_Babe_Ruth_Collection --destdir /downloads --verify-only

## Web UI (optional, enabled by default)
- Default port: 17865 (change via `WEB_PORT`).
- Compose example: `docker-compose up` then open http://localhost:17865.
- Queue multiple IA identifiers, start/stop jobs, view live logs/history.
- Mock runner available via `WEB_RUNNER=mock` for testing without network.

## Quick concepts
- **Native Python API**: This utility uses the `internetarchive` Python library directly for downloads, providing better control over parallelism and error handling than the standard CLI.
- **State Management**: It maintains a `.ia_status/` directory within the destination folder. This contains JSON files tracking `pending` and `done` files, allowing the tool to resume interrupted downloads perfectly.
- **Metadata Caching**: To reduce API pressure on IA, the tool fetches item metadata once and stores it locally as a "manifest" in `.ia_status/metadata.json`. This manifest is used for all subsequent logic (filtering, estimation, and verification).
- **Native Throttling**: Bandwidth capping uses a native Python implementation (Token Bucket algorithm), ensuring portability across all architectures (amd64/arm64) without external dependencies like `trickle`.
- **Health Monitoring**: A minimal web server (default port 8080) exposes the current `report.json` in real-time, allowing for easy integration with monitoring tools. The Web UI runs separately on port 17865 by default.
- **Advanced Sync**: When enabled via `IA_SYNC`, the tool performs a true mirror by deleting local files that are no longer present in the remote IA item.

# Development

## Build (local)

Build a single-arch image (defaults to `IA_PYPI_VERSION=5.7.1`):

```bash
docker build -t themorgantown/ia-mirror:0.4.0 -f docker/Dockerfile docker
docker build --pull --rm -f docker/Dockerfile -t ia-mirror:local docker
```

Multi-arch build (recommended for publishing):

```bash
docker buildx create --use --name ia-builder || true
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg IA_PYPI_VERSION=5.7.1 \
  -t themorgantown/ia-mirror:0.4.0 --push -f docker/Dockerfile docker
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

  ### Batch mode (multiple items from CSV)

  Create a CSV with two columns: `source` (IA identifier) and `destdir` (target path inside container):

  `batch_source.csv`
Note only source and destdir are required; other columns are optional:
  ```
  source,destdir,glob,exclude,format,concurrency,verify_mode
  The_Babe_Ruth_Collection,/downloads/The_Babe_Ruth_Collection,*.mp3,,mp3,10,checksum
  jillem-full-archive,/downloads/jillem-full-archive,,*_thumb.jpg,,5,size
  ```

  Run the container with batch mode enabled (mount the CSV and set dest roots as needed):

  ```bash
  docker run --rm \
    -v "$PWD/mirror:/downloads" \
    -v "$PWD/batch_source.csv:/app/batch_source.csv:ro" \
    -e IA_ACCESS_KEY=AKXXX -e IA_SECRET_KEY=SKYYY \
    themorgantown/ia-mirror:latest --use-batch-source --batch-source-path /app/batch_source.csv
  ```
  All other flags (e.g., `-g`, `-x`, `-f`, `-j`, `--checksum`) apply to every row in the CSV.

Recommended for production: use Docker secrets or your orchestration's secret mechanism and inject into the container as env vars or bind a single secret file as `/run/secrets/ia.ini` then copy into `/home/app/.config/ia/ia.ini` at startup.

## Config / ENV variables
- IA_IDENTIFIER (required) — item or collection identifier
- IA_DESTDIR — destination directory under /downloads (container resolves path)
- IA_GLOB (-g) — include glob; can be repeated or comma-separated (default `*`)
- IA_EXCLUDE (-x) — exclude glob(s); can be repeated or comma-separated
- IA_FORMAT (-f) — restrict to extensions (e.g., `mp3,flac`)
- IA_CONCURRENCY (-j) — parallel workers (default 5)
- IA_CHECKSUM — enable checksums (alias for `IA_VERIFY_MODE=checksum`)
- IA_VERIFY_MODE — verification level: `exists`, `size` (default), or `checksum`
- IA_DRY_RUN — dry-run
- IA_VERIFY_ONLY — only verify existing files
- IA_ESTIMATE_ONLY — only output size/time/cost estimates and exit
- IA_COLLECTION — treat identifier as collection
- IA_RESUMEFOLDERS — only consider .zip files and skip any zip whose folder already exists
- IA_SYNC — if truthy, enables sync mode (deletes local files not present in IA)
- IA_MAX_MBPS (optional) — bandwidth cap in Mbps; default off. Only applies if set (>0). Uses native Python throttling.
- IA_HEALTH_PORT — port for the internal health check server (default 8080; set to 0 to disable)
- IA_ASSUMED_MBPS — used for ETA/estimate math when uncapped (default 100)
- IA_COST_PER_GB — include egress cost estimate
- IA_SOURCE — filter by source type: `original`, `derivative`, or `metadata`
- IA_ON_THE_FLY — enable dynamic zip generation
- IA_XML_NAMES — use filenames from metadata XML
- IA_IGNORE_EXISTING — skip files if they exist without size/checksum check
- IA_NO_DIRECTORIES — flatten download into a single directory
- IA_FORCE_METADATA_UPDATE — force refresh of local metadata manifest
- Polite backoff (enabled by default):
  - IA_NO_BACKOFF — set truthy to disable exponential backoff
  - IA_BACKOFF_BASE, IA_BACKOFF_MAX, IA_BACKOFF_MULTIPLIER, IA_BACKOFF_JITTER — tune backoff (defaults: 2s, 60s, 2.0, 0.25)
- IA_LOG_LEVEL — INFO/DEBUG/ERROR (controls stdout/file logging)
- IA_NO_LOCK — set truthy to disable lockfile (not recommended)
- IA_USE_BATCH_SOURCE — truthy to enable batch mode
- IA_BATCH_SOURCE_PATH — path to batch CSV (default `./batch_source.csv`)
- IA_ACCESS_KEY / IA_SECRET_KEY — short-lived env-based credentials

 
## Files & outputs
- Download destination: `/downloads/<identifier>` (unless `--destdir` provided)
- Status dir: `/downloads/<identifier>/.ia_status/<identifier>.json`
- Lockfile: `/downloads/<identifier>/.ia_status/lock.json` (auto-removed on exit)
- Snapshot report: `/downloads/<identifier>/report.json`
- Log file: `/downloads/<identifier>/ia_download.log` (also streamed to stdout)

## Volume Layout

The container uses two primary volumes:
- **/downloads**: The destination for all downloaded content, logs, and job reports. Map this to a host directory with enough storage space.
- **/data**: Stores persistent application state, specifically the SQLite database (`ui.db`) for the Web UI queue and history. Map this to a host directory to preserve your job history across container restarts.

Note on `--destdir` layout: the underlying `ia` CLI writes files under `<destdir>/<identifier>/...`. When you set `IA_DESTDIR=/downloads`, this wrapper resolves the working directory to `/downloads/<identifier>` for logs/status, and instructs `ia` to write to `/downloads` so files land in `/downloads/<identifier>/...` (no double-nesting). Using the examples above will produce the expected layout.

Report behavior:
- `--dry-run` now writes a structured `report.json` summarizing totals and simulated ETA.
- `--estimate-only` writes a structured `report.json` with known/remaining bytes and estimated seconds, then exits without downloading.

## Troubleshooting
- If the image cannot find `ia`, ensure the `internetarchive` package version installed in the image provides the `ia` CLI (we pin with `IA_PYPI_VERSION` build arg). You can also bind a local `ia` binary into `/app/ia`.
- If you hit an issue, make an issue: https://github.com/themorgantown/ia-mirror/issues

# Security

This project is provided as-is without warranties. When using credentials, ensure they are handled securely (e.g., avoid mounting host config with sensitive data in shared environments). Consider using short-lived credentials or Docker secrets for production use.

## Security Scanning

This project uses [Docker Scout](https://docs.docker.com/scout/) to monitor for vulnerabilities.

### Automated Scanning
A GitHub Action runs monthly to scan the image. If high-severity vulnerabilities are found or if the base image has critical updates, a GitHub Issue is automatically created with a remediation report.

### Local Scanning
You can run Docker Scout locally to check the image before pushing:

```bash
# Build the image
docker build -f docker/Dockerfile -t ia-mirror:local docker

# Quick overview
docker scout quickview ia-mirror:local

# Detailed CVE scan
docker scout cves ia-mirror:local

# Check for base image updates
docker scout recommendations ia-mirror:local
```

## Testing

The project includes a consolidated test suite that runs:
1. Python Unit Tests (backend logic)
2. Command Line Integration Tests
3. Web UI Integration Tests

To run the full suite:

```bash
./tests/run_tests.sh
```

The script will automatically build a test Docker image and run all tests against it. Output is stored in `tests/test_output/`.

