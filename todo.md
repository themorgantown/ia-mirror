## Project Overview: `ia-mirror`

`ia-mirror` is a robust, Dockerized utility designed to mirror or download items from the Internet Archive (IA) at scale. It acts as a sophisticated wrapper around the official `internetarchive` Python library and its `ia` CLI.

### How it Works:
1.  **Configuration**: It uses a hybrid approach where environment variables (prefixed with `IA_`) are automatically mapped to CLI arguments. This makes it highly portable for Docker and CI/CD environments.
2.  **State Management**: It maintains a `.ia_status/` directory within the destination folder. This contains JSON files tracking `pending` and `done` files, allowing the tool to resume interrupted downloads perfectly.
3.  **Parallelism**: It uses a `ThreadPoolExecutor` to manage multiple concurrent `ia download` processes. This bypasses some of the single-threaded limitations of the standard CLI.
4.  **Resilience**: It implements a custom exponential backoff system to handle `429 Too Many Requests` or `5xx` server errors gracefully, which is critical for large-scale mirroring.
5.  **Reporting**: It generates a `report.json` and a detailed log file (`ia_download.log`) for every run, providing structured data on what was downloaded, skipped, or failed.

---

## 5 Ways to Improve `ia-mirror`

### 1. Complete IA Download API Coverage
The current implementation is missing several key flags available in the [official IA download API](https://archive.org/developers/internetarchive/cli.html#download). Adding support for `--source` (to filter by original vs. derivative), `--on-the-fly` (for dynamic zipping), and `--xml-names` would make it a truly "complete" mirror tool.

### 2. Native Python Bandwidth Limiting (Completed)
Bandwidth capping now uses a native Python implementation (Token Bucket algorithm) instead of the external `trickle` utility. This increases portability and reliability across different environments and architectures.

### 3. Metadata-First Architecture
Instead of fetching file lists and sizes repeatedly, the tool could fetch the entire item metadata once, store it locally as a "manifest," and use that for all subsequent logic (filtering, estimation, and verification). This reduces API pressure on IA and speeds up the "startup" phase of large downloads. Assume the metadata is stored in `.ia_status/metadata.json` and create a command to force update for the metadata when needed, or after 30 days by default. 

### 4. Advanced Sync & Cleanup Mode (Completed)
A "Sync" mode has been implemented that compares the local directory to the remote IA item and deletes local files that are no longer part of the remote item, ensuring a true mirror. Use `--sync` flag to enable this mode.

---

## TODO List: Improving the Tool

### API Coverage (High Priority)
- [x] **Add `--source` support**: Allow users to filter by `original`, `derivative`, or `metadata` via `IA_SOURCE`.
- [x] **Add `--on-the-fly` support**: Enable dynamic zip generation for items that support it.
- [x] **Add `--xml-names` support**: Use the filenames as defined in the metadata XML rather than the filesystem names.
- [x] **Add `--ignore-existing` support**: A faster skip mode that doesn't check file size or checksums if the file exists.
- [x] **Add `--no-directories` support**: Allow flattening the download into a single directory.

### Robustness & Features
- [x] **Native Throttling**: Implement a Python-based rate limiter to replace the `trickle` dependency.
- [x] **Manifest Persistence**: Save the full `ia metadata` output to `.ia_status/metadata.json` to avoid redundant API calls.
- [x] **Validation Levels**: Implement `--verify-mode` with options: `exists` (fastest), `size` (default), and `checksum` (thorough).
- [x] **Batch CSV Enhancements**: Support additional columns in `batch_source.csv` for per-item overrides (e.g., different concurrency or globs per row).
- [x] **Health Check Endpoint**: Add a minimal web server to expose `report.json` data in real-time.

### Maintenance
- [x] **Dependency Update**: Ensure `internetarchive` is pinned to the latest stable version in `requirements.txt`.
- [x] **Multi-Arch Verification**: Ensure the `trickle` replacement works correctly on both `amd64` and `arm64` Docker builds.
