# Changelog

## [1.1.8] - 2026-08-24

### Dependencies
- Bump base image `python` 3.14.6-alpine3.24 → 3.14.7-alpine3.24
- Bump `internetarchive` 5.11.0 → 5.11.1 — task-log retrieval now goes to `archive.org` instead of `catalogd.archive.org`, which is VPN-restricted; `ia tasks --get-task-log` previously timed out for everyone outside Archive.org
- Bump `gunicorn` 26.0.0 → 26.2.0 — includes an HTTP/2 security fix. The new cleartext-HTTP/2 (`http2_cleartext`) option defaults to `off`, so the served protocol is unchanged
- Bump vendored `Socket.IO` client 4.8.1 → 4.8.3 (`docker/static/vendor/socket.io.min.js`)
- `ARG IA_PYPI_VERSION` in `docker/Dockerfile`, the `IA_PYPI_VERSION` build arg in `release-buildx.yml`, and the README build example all track `requirements.txt` at 5.11.1
- `Flask` 3.1.3, `Flask-CORS` 6.0.5, `Flask-SocketIO` 5.6.1, `python-socketio` 5.16.4, `pip` 26.2.1, `pytest` 9.1.1, `pytest-flask` 1.3.0, and vendored `Bootstrap` 5.3.8 are already at their latest releases — unchanged

### CI/CD
- Bump `hadolint/hadolint-action` 3.4.0 → 3.5.0 (ships hadolint 2.15.1). `docker/Dockerfile` lints clean against 2.15.1 with the existing `DL3008` ignore
- Bump `docker/setup-buildx-action` 4.2.0 → 4.3.0
- Bump `actions/setup-python` 6.3.0 → 7.0.0. The major bump drops the `pip-install` input, which `ci.yml` never used
- All actions remain pinned to immutable commit SHAs

### Tooling
- Fix three developer commands and the matching VS Code task that could never have worked: `docker run ia-mirror:local --print-effective-config` and the `test-item` dry-run both hang forever, because `entrypoint.sh` defaults `WEB_ENABLED=true` and in that mode `exec`s Gunicorn and discards the CLI arguments. All now pass `-e WEB_ENABLED=false`.
- Fix the "Run pip audit" VS Code task. It shelled into the image to `pip install pip-audit`, but pip was removed from the image in v1.1.7, so the task silently produced no output and no failure. It now audits `docker/requirements.txt` from an isolated host venv, matching `ci.yml`.
- Replace hardcoded `/Users/daniel/...` paths in `.vscode/tasks.json` with `${workspaceFolder}`.
- Add `/upgrade`, `/increment-version`, and `/release-prep` slash commands, and rewrite the five existing ones with frontmatter, explicit success criteria, and the build-cache and `WEB_ENABLED` gotchas documented inline.
- Correct a CHANGELOG heading typo: the v1.1.6 section was labelled `[1.1.16] - 2026-08-3`, which sorted out of order and named a version that was never tagged.

### Documentation
- Re-audit the "What ia-mirror Adds Beyond `internetarchive`" table against `internetarchive` 5.11.1; several "Not native" claims had gone stale:
  - Upstream's command list now includes `flag` and `simplelists`, which the intro paragraph omitted.
  - "Estimate and cost reporting — Not native" was wrong: `ia download --dry-run` exists. Only the size/time/cost estimation is additive.
  - "Verify-only mirror checks" now names upstream's `-C/--checksum` and `--checksum-archive`, which skip during a download; the standalone verify pass is still the additive part.
  - "Polite global backoff controls" now notes upstream honors `Retry-After` (added in 5.6.0).
  - "Built-in parallel mirror workers" now distinguishes upstream's `range_jobs` (5.10.0), which parallelizes byte ranges within one file, from multi-item concurrency, which upstream still lacks.
  - The table now states which `internetarchive` version it compares against.
- Note two inherited upstream behaviors: downloads have not counted toward archive.org view counts since 5.9.0 (`cnt=0` by default, and `ia-mirror` does not expose the `--count-views` opt-in), and `--range`/`--stdout` partial fetches are upstream-only.
- Document `IA_USER_AGENT_SUFFIX`. `docker/entrypoint.sh` has supported it since `internetarchive` 5.7.2 — writing it to the `[general]` section of `ia.ini` in both runtime modes — but neither the README nor `docker/example.env` mentioned it. Both now document it.

## [1.1.7] - 2026-08-16

### Security
- Bump base image `python` 3.14.6-alpine3.23 → 3.14.6-alpine3.24. Alpine 3.23 ships `sqlite-libs` 3.51.2-r0, which has no fixed release for CVE-2026-11822 and CVE-2026-11824 (both High, reported by Docker Scout in #43 and #49). Alpine 3.24 ships 3.53.2-r0, above the `<=3.51.2-r0` affected range for both. Docker Scout rates the 3.24 base at 0 critical / 0 high / 0 medium / 0 low.
- Remove `pip` and `ensurepip` from the final image. After the sqlite fix, pip's vendored dependency SBOM (`pip/_vendor/bom.cdx.json`, declaring msgpack 1.1.2 and setuptools 70.3.0) was the only remaining source of High findings — CVE-2026-57585 / GHSA-6v7p-g79w-8964 and CVE-2025-47273. pip 26.2.1 still vendors both, so there is no version to upgrade to. Nothing at runtime imports pip, so it is uninstalled after `requirements.txt` is installed. The published image now scans 0C / 0H / 0M / 0L and is 35 MB instead of 42 MB.
  - Consequence: `pip install` no longer works inside a running container. Add packages to `docker/requirements.txt` and rebuild.

### Dependencies
- Bump `pip` 26.2 → 26.2.1 and `python-socketio` 5.16.3 → 5.16.4 (automated biweekly update, commit 58437f4 — landed on `main` unreleased)

### Fixed
- `VERSION` and `ARG PROJECT_VERSION` in `docker/Dockerfile` were left at 1.1.4 through the v1.1.5 and v1.1.6 tags, so the `org.opencontainers.image.version` label on those images was wrong. Both now read 1.1.7.
- The Web UI integration test (`tests/runtests.sh`) never actually ran. It published the container port but did not pass `WEB_HOST=0.0.0.0`, so Gunicorn stayed bound to the container's loopback and every request from the host timed out — the whole test reported as "Container failed to start".
- With that fixed, the same test exposed a race: it waited for `queue_length` to reach 0 before checking job history, but a job leaves the queue when it *starts* running, not when it finishes. It now polls `/api/jobs` for a terminal (`completed`/`failed`) status.

## [1.1.6] - 2026-08-03

### Fixed
- **Web UI jobs were silently rewritten by leftover CLI environment variables.** `fetcher.py` applied `IA_*` settings on top of arguments it had already been given, and the boolean switches were one-way — nothing on the command line could turn one back off. A stale `IA_RESUMEFOLDERS=1` in `docker/live.env` therefore forced every queued download into resumefolders mode, which only ever matches `*.zip`; any item without a zip failed with `❌ No matching files.` and exit code 1 while the UI showed the job's glob as `*`. `IA_COLLECTION=1` leaked the same way, sending single items down the collection lookup path first.
- Job-configuring `IA_*` variables are now stripped from the fetcher subprocess in Web UI mode (`web/jobs.py`, `JOB_CONFIG_ENV_VARS`). A job's own config is the only thing that describes it. Credentials and operational tuning the UI does not expose (`IA_ACCESS_KEY`, `IA_SECRET_KEY`, `IA_LOG_LEVEL`, `IA_DOWNLOAD_RETRIES`, `IA_BACKOFF_*`, timeouts) still pass through.
- On the command line, `IA_*` variables are now strictly *defaults*: an explicitly passed argument always wins (`--glob '*'` beats `IA_GLOB=*.zip`). Options left unspecified are still seeded from the environment, so existing CLI setups behave as before.
- Correct the placeholder-identifier error message, which told Web UI users to edit `IA_IDENTIFIER` in `docker-compose.yml` — a variable that no longer affects their jobs.

### Changed
- Extract `build_parser()` out of `main()` in `fetcher.py` so tests exercise the real options and dests instead of a hand-maintained copy.
- `docker/example.env` marks the download-settings block CLI-mode-only; it previously advertised that those values "can also seed Web UI job defaults", which is exactly the behaviour that caused the bug.
- README documents that Web UI jobs ignore `IA_*` job settings, and that explicit CLI arguments beat the environment.

## [1.1.4] - 2026-08-03

### Dependencies
- Bump base image `python` 3.14.5-alpine3.23 → 3.14.6-alpine3.23
- Bump `pip` 26.1.2 → 26.2
- Bump `internetarchive` 5.9.0 → 5.11.0
- Bump `python-socketio` 5.16.2 → 5.16.3
- Bump `pytest` 9.1.0 → 9.1.1
- Bump vendored `Bootstrap` 5.3.0 → 5.3.8 (`docker/static/vendor/`)

### CI/CD
- Bump `actions/checkout` 6.0.3 → 7.0.1 (runs on the Node 24 runner)
- Bump `actions/setup-python` 6.2.0 → 6.3.0
- Bump `docker/login-action` 4.2.0 → 4.6.0
- Bump `docker/setup-qemu-action` 4.1.0 → 4.2.0
- Bump `docker/setup-buildx-action` 4.1.0 → 4.2.0
- Bump `docker/build-push-action` 7.2.0 → 7.3.0
- Bump `hadolint/hadolint-action` 3.3.0 → 3.4.0
- All actions remain pinned to immutable commit SHAs

### Fixed
- `VERSION` was stuck at 1.0.3 and `ARG PROJECT_VERSION` in `docker/Dockerfile` was stuck at 1.0.3 — both now track the released version, so the `org.opencontainers.image.version` label on default builds is no longer wrong
- `ARG IA_PYPI_VERSION` in `docker/Dockerfile`, the `IA_PYPI_VERSION` build arg in `release-buildx.yml`, and the build example in the README were all pinned to 5.8.0 while `requirements.txt` had moved on — all now read 5.11.0
- Rewrite the `HEALTHCHECK` command in JSON exec form (`["sh", "-c", ...]`) — hadolint 3.4.0 promoted `DL3025` to a CI failure on the previous shell form. Behaviour is unchanged: the probe still exits 0 when the API answers and 1 when it does not

## [1.1.3] - 2026-06-15

### Fixed
- Replace `curl` with a Python `urllib` call in the container `HEALTHCHECK` — the Alpine base image ships no `curl`, so the healthcheck was failing on every probe
- Remove `coreutils` from the Dockerfile package list and pin `pip` in `requirements.txt`

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
