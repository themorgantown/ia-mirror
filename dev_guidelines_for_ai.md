# Dev Guidelines for AI Coding Agents (ia-mirror)

Single source of truth for AI assistants in this repo. This merges the guidance from `CLAUDE.md` and `.github/copilot-instructions.md`.

## Overview
ia-mirror is a Docker-first Internet Archive mirroring utility. The primary deliverable is a production-ready container that wraps the `ia` CLI with parallel downloads, resume, status tracking, and logging. Secondary Python tools support analysis and ZIP/audio processing.

## Architecture
- Primary component: `docker/`
  - `fetcher.py` — Python wrapper: env→CLI injection, graceful shutdown, status persistence, collection enumeration, small reporting
  - `entrypoint.sh` — credential setup and exec
  - `Dockerfile` — Python slim base, non-root user (app:1000), tini init
- Secondary tools: `python tools/` — analysis and ZIP processing (not required for normal runs)

### Data flow & outputs
1) Downloads → `/data/<identifier>/`
2) Status → `/data/<identifier>/.ia_status/<identifier>.json`
3) Snapshot → `/data/<identifier>/report.json`
4) Logs → `/data/<identifier>/ia_download.log` (also stdout)

## Essential workflows
- Build (local):
  - Single-arch: `docker build -t ia-mirror:local -f docker/Dockerfile docker`
  - Multi-arch: `docker buildx build --platform linux/amd64,linux/arm64 -t themorgantown/ia-mirror:<ver> --push -f docker/Dockerfile docker`
- Run (env-driven):
  - `docker run --rm -v "$PWD/mirror:/data" -e IA_IDENTIFIER=some-item -e IA_ACCESS_KEY=xxx -e IA_SECRET_KEY=yyy ia-mirror:local`
- Collection mode: set `IA_COLLECTION=1`

## Project conventions
- Env→CLI injection (when no explicit args):
  - booleans: `IA_DRY_RUN`, `IA_CHECKSUM`, `IA_VERIFY_ONLY`, `IA_COLLECTION`, ...
  - values: `IA_DESTDIR`, `IA_CONCURRENCY` (-j), `IA_GLOB` (optional; unset = all files), `IA_MAX_MBPS`/`IA_MAX_Mbps`, `IA_ASSUMED_MBPS`/`IA_ASSUMED_Mbps`
  - prefer `IA_IDENTIFIER` (legacy: `IA_ITEM_NAME`)
- Destdir layout: set `IA_DESTDIR=/data` to avoid double-nesting; wrapper aligns logs/status alongside the identifier
- Status persistence: `.ia_status` + `report.json`; graceful SIGTERM snapshot for resume

## Authentication patterns
- Env vars (recommended): `IA_ACCESS_KEY` + `IA_SECRET_KEY` → entrypoint writes `/home/app/.config/ia/ia.ini`
- Host config mount: `-v ~/.config/ia:/home/app/.config/ia:ro`
- Docker secrets: mount `/run/secrets/ia.ini` and copy to config at start

## Key configuration
- Volume mount: `/data`
- Required: `IA_IDENTIFIER` (or provide as CLI arg)
- Common envs: `IA_CONCURRENCY`, `IA_DRY_RUN`, `IA_DESTDIR`, `IA_CHECKSUM`, `IA_LOG_LEVEL`, `IA_ACCESS_KEY`, `IA_SECRET_KEY`
- Build arg: `IA_PYPI_VERSION` to pin the `internetarchive` version

## CI / Release
- Workflow: `/.github/workflows/release-buildx.yml` builds multi-arch (amd64/arm64) and pushes to GHCR and Docker Hub
- Auth:
  - GHCR: `GITHUB_TOKEN` with packages:write
  - Docker Hub: repo secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`
- Versioning & tags:
  - Trigger by pushing a git tag like `v0.1.3`
  - GHCR tags: `v0.1.3` and `latest`
  - Docker Hub tags: `0.1.3` (no `v`) and `latest`

## Files to inspect
- `docker/fetcher.py`, `docker/entrypoint.sh`, `docker/Dockerfile`, `docker/example.env`
- `.github/workflows/release-buildx.yml`, `.dockerignore`

## Quick example (dry-run)
```
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_DESTDIR=/data \
  -e IA_DRY_RUN=true \
  ia-mirror:local
```

## Development & troubleshooting
- Python syntax check: `python -m py_compile docker/fetcher.py`
- Lint Dockerfile: `hadolint docker/Dockerfile`
- Print effective config: `docker run --rm ia-mirror:local --print-effective-config`
- If `ia` not found: check `IA_PYPI_VERSION` and `internetarchive` install
- Permissions: container runs as `app:1000`; ensure volume write access

## Security
- Don’t bake creds into images; use tokens/secrets
- Entry point writes `ia.ini` from env/secrets; never echo secret contents
- Sensitive files (ia.ini) use restrictive perms

