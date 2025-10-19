# ia-mirror Development Guide

Single source of truth for AI coding assistants working on this Internet Archive mirroring utility.

## Project Overview
ia-mirror is a Docker-first Internet Archive mirroring utility. The primary deliverable is a production-ready container that wraps the `ia` CLI with parallel downloads, resume capability, status tracking, and comprehensive logging.

## Common Commands

### Build Commands
- Build local image: `docker build -t ia-mirror:local -f docker/Dockerfile docker`
- Multi-arch build: `docker buildx build --platform linux/amd64,linux/arm64 -t themorgantown/ia-mirror:<ver> --push -f docker/Dockerfile docker`
- Run VS Code build task: Use the "Build ia-mirror image" task in tasks.json

### Development Commands
- Syntax check: `python -m py_compile docker/fetcher.py`
- Lint Dockerfile: `hadolint docker/Dockerfile`
- Print config: `docker run --rm ia-mirror:local --print-effective-config`
- Test run (dry): `docker run --rm -v "$PWD/mirror:/data" -e IA_IDENTIFIER=test-item -e IA_DRY_RUN=true ia-mirror:local`

### Git & Release Commands
- Check status: `git status`
- Create release: `git tag -a v0.2.3 -m "Release v0.2.3" && git push origin v0.2.3`
- Fix bad tag: `git push origin :refs/tags/0.2.3; git tag -d 0.2.3; git tag -a v0.2.3 -m "Release v0.2.3"; git push origin v0.2.3`
- View releases: `gh release list`

## Architecture
- **Primary component**: `docker/`
  - `fetcher.py` — Python wrapper: env→CLI injection, graceful shutdown, status persistence, collection enumeration
  - `entrypoint.sh` — credential setup and process exec
  - `Dockerfile` — Python slim base, non-root user (app:1000), tini init
- **Secondary tools**: `python tools/` — analysis and ZIP processing utilities

### Data Flow & Outputs
1) Downloads → `/data/<identifier>/`
2) Status → `/data/<identifier>/.ia_status/<identifier>.json`
3) Snapshot → `/data/<identifier>/report.json`
4) Logs → `/data/<identifier>/ia_download.log` (also stdout)

## Versioning & Release Workflow

### Semantic Versioning
- Use format: MAJOR.MINOR.PATCH (e.g., 0.2.3)
- Git tags MUST have "v" prefix: v0.2.3
- CI triggers ONLY on tags matching v*.*.*
- Docker Hub strips "v" → themorgantown/ia-mirror:0.2.3
- GHCR keeps "v" → ghcr.io/themorgantown/ia-mirror:v0.2.3

### Release Process
1. **Commit changes**:
   ```bash
   git checkout main
   git pull --ff-only
   # Make your changes
   git add -A
   git commit -m "Your descriptive message"
   git push origin main
   ```

2. **Tag release** (note v-prefix):
   ```bash
   git tag -a v0.2.3 -m "Release v0.2.3"
   git push origin v0.2.3
   ```

3. **Automatic CI**: GitHub Actions builds multi-arch images and pushes to both registries

## Essential Workflows

### Local Development
- **Build single-arch**: `docker build -t ia-mirror:local -f docker/Dockerfile docker`
- **Multi-arch build**: `docker buildx build --platform linux/amd64,linux/arm64 -t themorgantown/ia-mirror:<ver> --push -f docker/Dockerfile docker`
- **Test run**: `docker run --rm -v "$PWD/mirror:/data" -e IA_IDENTIFIER=some-item -e IA_ACCESS_KEY=xxx -e IA_SECRET_KEY=yyy ia-mirror:local`
- **Collection mode**: Set `IA_COLLECTION=1`

## Project conventions
- Env→CLI injection (when no explicit args):
  - booleans: `IA_DRY_RUN`, `IA_CHECKSUM`, `IA_VERIFY_ONLY`, `IA_COLLECTION`, ...
  - values: `IA_DESTDIR`, `IA_CONCURRENCY` (-j), `IA_GLOB`, `IA_MAX_MBPS`/`IA_MAX_Mbps`, `IA_ASSUMED_MBPS`/`IA_ASSUMED_Mbps`
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

## CI/CD Pipeline
- **Workflow**: `.github/workflows/release-buildx.yml`
- **Platforms**: Multi-arch (amd64/arm64)
- **Registries**: GHCR + Docker Hub
- **Authentication**:
  - GHCR: `GITHUB_TOKEN` with packages:write
  - Docker Hub: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` secrets
- **Tag Behavior**:
  - Push `v0.2.3` → triggers build
  - GHCR: `v0.2.3` and `latest`
  - Docker Hub: `0.2.3` (strips v) and `latest`

### Fixing Release Issues
- **Wrong tag format** (missing v):
  ```bash
  git push origin :refs/tags/0.2.3
  git tag -d 0.2.3
  git tag -a v0.2.3 -m "Release v0.2.3"
  git push origin v0.2.3
  ```
- **Redo version**:
  ```bash
  git push origin :refs/tags/v0.2.3
  git tag -d v0.2.3
  git tag -a v0.2.3 -m "Release v0.2.3"
  git push origin v0.2.3
  ```

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

## Core Files to Understand
- `docker/fetcher.py` — Main Python wrapper script
- `docker/entrypoint.sh` — Container initialization and credential setup  
- `docker/Dockerfile` — Container build definition
- `docker/example.env` — Environment variable template
- `.github/workflows/release-buildx.yml` — CI/CD pipeline
- `python tools/` — Analysis utilities (optional)

## Development & Troubleshooting
- **Syntax check**: `python -m py_compile docker/fetcher.py`
- **Lint Dockerfile**: `hadolint docker/Dockerfile`
- **Test config**: `docker run --rm ia-mirror:local --print-effective-config`
- **Missing ia CLI**: Check `IA_PYPI_VERSION` and `internetarchive` install
- **Permissions**: Container runs as `app:1000`; ensure host volume write access
- **Build issues**: Check Docker daemon, buildx setup, and registry auth

## Security Guidelines  
- **NEVER** bake credentials into images
- Use environment variables or Docker secrets for auth
- `entrypoint.sh` writes `ia.ini` from env (never echo secrets)
- Sensitive files get restrictive permissions (600)
- Use non-root user (`app:1000`) in container
- Review `.dockerignore` to prevent secret leaks

## Testing Patterns
- **Dry run**: Always test with `IA_DRY_RUN=true` first
- **Small items**: Use test items for development
- **Volume mounts**: Test both read-only and read-write scenarios  
- **Multi-platform**: Test on both amd64 and arm64 if possible
- **Error scenarios**: Test network failures, auth issues, disk space

## Claude Workspace Setup

### Custom Slash Commands Available
- `/project:build-local` — Build local Docker image
- `/project:release <version>` — Create release tag and trigger CI
- `/project:test-item <identifier>` — Test container with IA item (dry run)
- `/project:quality-check` — Run comprehensive code quality checks
- `/project:build-multiarch <version>` — Manual multi-arch build/push

### Recommended Tool Permissions
Use `/permissions` command in Claude to allow these tools:
- `Edit` — File editing
- `Bash(git *)` — All git operations  
- `Bash(docker *)` — Docker build/run/management
- `Bash(gh *)` — GitHub CLI operations
- `Bash(python *)` — Python syntax checking
- `Bash(hadolint *)` — Dockerfile linting
- `Read` — File reading
- `FetchURL` — Documentation fetching

### Useful Domains to Allowlist
- `github.com` — Repository access
- `docs.docker.com` — Docker documentation
- `hub.docker.com` — Docker Hub registry
- `archive.org` — Internet Archive API docs
- `pypi.org` — Python package documentation

