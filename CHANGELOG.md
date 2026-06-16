# Changelog

## [1.1.2] - 2026-06-15

### CI/CD
- Bump `peter-evans/create-issue-from-file` from 5.0.1 to 6.0.0 in Docker Scout monitor workflow

## [1.1.1] - 2026-06-15

### Dependencies
- Bump `internetarchive` 5.8.0 → 5.9.0
- Bump `Flask-CORS` 6.0.2 → 6.0.5
- Bump `python-socketio` 5.16.1 → 5.16.2
- Bump `pytest` 9.0.3 → 9.1.0

## [1.1.0] - 2026-06-12

### Security
- Default `WEB_HOST` to `127.0.0.1` instead of `0.0.0.0` in `entrypoint.sh` — prevents accidental network exposure when running without a reverse proxy
- Redact `ia_access_key` and `ia_secret_key` from `GET /api/config` responses — credentials no longer leak through the status API
- Pin all GitHub Actions workflows to exact commit SHAs — hardens supply chain against tag-mutable third-party actions
- Add `CODEOWNERS` file requiring review on all workflow file changes
- Add HTTP security headers to every response: `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`
- Add `.gitleaks.toml` with IA credential scanning rules to catch accidental secret commits

### Features
- **Job log persistence**: Store up to 500 log lines per job in a new `job_logs` SQLite table — clients that refresh mid-job or reconnect can retrieve buffered output instead of missing it
- **New endpoint** `GET /api/jobs/<id>/logs` — returns buffered log lines for any job, live or completed
- **Orphaned job recovery**: On worker startup, reset any jobs stuck in `running` state to `failed` with message "Interrupted: worker restarted" — prevents silently blocked queues after crashes or container restarts
- **Per-file retry with exponential backoff**: `download_single_file` retries failed downloads up to `IA_DOWNLOAD_RETRIES` times (default 3), with delay doubling from `IA_RETRY_BACKOFF_BASE` seconds (default 5s); respects shutdown signal during sleep
- **CORS support**: Web API now accepts cross-origin requests via `Flask-CORS`
- Improved path validation across file browser API endpoints
- **Configurable host download directory**: `GET /api/config` returns `host_download_dir` (derived from `DOWNLOAD_DIR` env); `POST /api/config` persists a desired-next value; UI settings modal shows the current path, accepts a new value, and displays a restart-required snippet with a copy button — clarifies that changing the mount requires editing `.env` and restarting
- Add `.env.example` for `DOWNLOAD_DIR`/`DATA_DIR`; `docker-compose.yml` uses these vars for volume mounts

### UI
- Switch from dark terminal aesthetic to a clean light theme throughout the web UI
- Fix operation dropdown visibility — was invisible against the dark custom background; now uses standard browser `<select>` appearance with visible arrow
- All form controls (inputs, selects, buttons) use standard browser styling: white background, gray border, blue focus ring
- Buttons use 4px border-radius with proper solid and ghost variants
- Terminal and progress output areas intentionally retain dark styling for readability
- Remove `data-bs-theme=dark` from modals
- Update brand kicker text

### Fixed
- `docker-compose.yml` now sets `WEB_HOST=0.0.0.0` inside the container and publishes the UI on `127.0.0.1:17865` — without this, the new `WEB_HOST` hardening default left the published port unreachable while the healthcheck still passed

### Documentation
- Expand README with a detailed feature comparison between ia-mirror and the base `internetarchive` library
- Add new environment variable examples to `example.env` (`IA_DOWNLOAD_RETRIES`, `IA_RETRY_BACKOFF_BASE`)
- Full README review: corrected `IA_CONCURRENCY` default (4, not 5) and `WEB_HOST` default (`127.0.0.1`); documented the `DOWNLOAD_DIR`/`.env` download-location workflow with macOS/Windows/Linux examples; added Windows shell notes, missing API endpoints (job logs, unlock, watcher, clear-history), and a troubleshooting entry for the healthy-but-unreachable UI case; moved the project description above the comparison table

### CI/CD
- Pin `docker-scout-monitor.yml`, `release-buildx.yml`, `sync-readme-to-dockerhub.yml`, and `ci.yml` workflow actions to immutable commit SHAs

---

## [1.0.3] - 2026-05-17

### Features
- **Enhanced Recent Downloads panel**: default look-back window expanded from 7 → 30 days; panel now shows only `completed` jobs (not queued/running); job cards display identifier, file size, destination path, and completion date
- **Switch to Alpine base image**: smaller Docker footprint, faster pulls

### Fixed
- `update_worker_state()` used `None` as a sentinel, making it impossible to explicitly clear the active job field; replaced with an `_UNSET` sentinel object
- Fixed `isRunning` assignment on queue-add in the JS client
- Fixed a null-guard missing on `percentEl` in the JS progress renderer
- Missing newline at end of `Dockerfile`

### Documentation
- Expand README with a detailed feature comparison between ia-mirror and the base `internetarchive` library

## [1.0.2] - 2026-05-15

### Testing
- **Test suite overhaul**: Replaced `test_ui.py`, `test_file_browser.py`, and `test_watcher.py` with structured new files — `test_fetcher.py`, `test_file_browser_api.py`, `test_watcher_service.py`, and `test_web_backend.py`; reworked `runtests.sh` for improved reliability
- Add `tests/test_web_backend.py` with coverage for the recent downloads API endpoint

### Developer Experience
- Add `scripts/check_dependencies.py` — validates all runtime Python dependencies are importable and reports missing packages
- Add `.vscode/tasks.json` with tasks for hadolint Dockerfile linting and Python syntax checking (`python -m py_compile`)
- Add `docker/example.env` listing all supported environment variables with comments
- Add `README_webui.md` with dedicated web UI documentation
- Add `.DS_Store` to `.gitignore`

### CI/CD
- **Enhanced Docker Scout workflow**: CVE and recommendations output written to structured files; exit code 2 treated as "vulnerabilities found" rather than a hard failure; issue creation conditioned on `vulns_found` output; recommendations wrapped in fenced block with empty/null handling
- Bump GitHub Actions versions across all workflows
- Dockerfile: use `--no-install-recommends` for system package installs

## [0.4.0] - 2025-12-19

### Architecture
- **Native Python API**: Replaced `ia` CLI subprocesses with direct `internetarchive` library usage for better performance and control.
- **Native Throttling**: Implemented Token Bucket algorithm for bandwidth limiting, removing `trickle` dependency.
- **Metadata Caching**: Local caching of item metadata in `.ia_status/metadata.json` to reduce API calls.

### Features
- **Advanced Sync**: New `--sync` / `IA_SYNC` mode to delete local files not present in the remote item.
- **Health Check Server**: New HTTP server (default port 8080) exposing real-time `report.json`.
- **Expanded API Support**: Added `--source`, `--on-the-fly`, `--xml-names`, `--ignore-existing`, `--no-directories`.
- **Verification Levels**: Added `--verify-mode` (`exists`, `size`, `checksum`).

### Security & Maintenance
- **Base Image**: Updated to `python:3.14-slim`.
- **Docker Scout**: Added monthly vulnerability scanning workflow.
- Bump hadolint/hadolint-action from 3.2.0 to 3.3.0
- Bump anchore/sbom-action from 0.20.5 to 0.20.6
- Bump docker/login-action from 3.5.0 to 3.6.0
- Bump peter-evans/dockerhub-description from 4.0.2 to 5.0.0

### Documentation
- Update version examples to v0.4.0 in CLAUDE.md

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
