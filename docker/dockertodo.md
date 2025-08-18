
- [x] Verified dependency versions (hash pinning via requirements freeze placeholder)

## Runtime Interface
- [x] Single ENTRYPOINT (Python wrapper) supporting:
- [x] IA_CHECKSUM
- [x] IA_DRY_RUN

Authenticate (host once):
5. Hooks (pre/post/final)
# Docker TODO (MVP Progress)

This file was the original TODO/notes for the Docker image; the authoritative documentation and usage guidance has been moved to the top-level `README.md`.

Key implemented items (migrated):

- Small base image (`python:3.12-slim`)
- Non-root runtime user (UID 1000)
- Pinned `internetarchive` via build ARG `IA_PYPI_VERSION`
- Env var → CLI arg injection and `--print-effective-config`
- `/data` volume and destination-based status directory (`.ia_status`)
- Graceful exit snapshot and `report.json` written to destination
- Stream logs to stdout (controlled via `IA_LOG_LEVEL`) and also write `ia_download.log` into destination

For full usage, examples, buildx command and environment variables see `../README.md`.
