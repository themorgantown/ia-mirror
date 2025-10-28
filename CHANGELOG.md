# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Dependencies
- Bump hadolint/hadolint-action from 3.2.0 to 3.3.0
- Bump anchore/sbom-action from 0.20.5 to 0.20.6
- Bump docker/login-action from 3.5.0 to 3.6.0
- Bump peter-evans/dockerhub-description from 4.0.2 to 5.0.0

### Documentation
- Update version examples to v0.2.3 in CLAUDE.md

## [0.3.0] - 2025-10-28

### Features
- Add multi-glob include support (`-g/--glob` repeatable and comma-separated)
- Add exclude filters (`-x/--exclude`) and extension filters (`-f/--format`)
- Add lockfile safety (default on) stored under `.ia_status/lock.json` with PID/host/uuid; `--no-lock` and `IA_NO_LOCK` to disable
- Emit structured `report.json` for `--dry-run` and `--estimate-only` with counts/sizes/ETA
- Implement polite exponential backoff with jitter on HTTP 429/5xx; defaults enabled and tunable via `--backoff-*` and env vars

### Behavior
- Bandwidth caps remain opt-in only; no throttling unless `--max-mbps` (or `IA_MAX_MBPS`) is set
- `resumefolders` continues to operate on `.zip` files and now honors excludes

### Configuration
- New envs: `IA_EXCLUDE`, `IA_FORMAT`, `IA_NO_LOCK`, `IA_NO_BACKOFF`, `IA_BACKOFF_BASE`, `IA_BACKOFF_MAX`, `IA_BACKOFF_MULTIPLIER`, `IA_BACKOFF_JITTER`

### Documentation
- README: document new flags/envs, lockfile location, and structured report behavior
- example.env: add commented examples for filters, lock override, and clarify bandwidth cap is off by default

## [0.3.1] - 2025-10-28

### Features
- Optional batch mode: `--use-batch-source` to process a two-column CSV (`source,destdir`) running multiple mirrors sequentially
- Env support: `IA_USE_BATCH_SOURCE`, `IA_BATCH_SOURCE_PATH`

### Documentation
- README: add batch mode usage and CSV example
- Add `batch_source.csv` example to repository root

## [0.2.2] - 2025-10-19

### Security
- Upgrade internetarchive from 5.5.1 to 5.7.0
- Fix critical directory traversal vulnerability
- Add automatic filename sanitization and path resolution checks

### Development
- Enhanced Claude AI workspace configuration
- Consolidated development documentation into CLAUDE.md
- Added custom slash commands for Docker workflows
- Removed obsolete development.md and dev_guidelines_for_ai.md files

## [0.2.1] - 2025-10-09

### Features
- Add IA_LOG_LEVEL and stream logs to stdout
- Status files moved to destination (`.ia_status/`) and snapshot `report.json` on exit or interruption
- Entrypoint now supports creating `~/.config/ia/ia.ini` from `IA_ACCESS_KEY`/`IA_SECRET_KEY` env vars

### Docker
- Dockerfile: pinned `internetarchive` via build ARG `IA_PYPI_VERSION`; set HOME/XDG_CONFIG_HOME
- Add Docker Compose support for local development
- Update Dockerfile to fix vulnerability

### Documentation
- README expanded with usage, buildx example, and config docs

### Dependencies
- Bump actions/checkout from 4 to 5
- Bump hadolint/hadolint-action from 3.1.0 to 3.2.0
- Bump actions/setup-python from 5.6.0 to 6.0.0

## [0.2.0] - 2025-09-18

### Features
- Improve logging in `fetcher.py` and `entrypoint.sh` to provide more context and aid in debugging
- Add dynamic ETA calculation and speed sampling for downloads

### Documentation
- Overhaul README.md to be more comprehensive and user-friendly
- Update version to 0.2.0
- Delete temporary logging documentation files

## [0.1.5] - 2025-09-18

### Features
- Update glob patterns in documentation
- Add Python tools for analysis and ZIP processing

### Tools
- Add parallel_ia_resume.py for resuming downloads
- Add zip_audio_archive_processor.py for audio archive processing

## [0.1.4] - 2025-09-05

### Security
- Rebuild with internetarchive 5.5.1 to address CVE-2025-58438

## [0.1.2] - 2025-09-03

### CI/CD
- Push Docker Hub tags (including 0.1.2) and remove GitHub release step
- Fix CI ignore patterns
- Update README synchronization

## [0.1.1] - 2025-09-02

### Initial Release
- Initial Docker implementation with Internet Archive CLI wrapper
- Basic CI/CD pipeline setup
- GitHub Actions workflows for multi-arch builds
- Docker Hub README synchronization
- Basic documentation and configuration files
