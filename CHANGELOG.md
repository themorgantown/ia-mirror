# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
- Add IA_LOG_LEVEL and stream logs to stdout
- Status files moved to destination (`.ia_status/`) and snapshot `report.json` on exit or interruption
- Entrypoint now supports creating `~/.config/ia/ia.ini` from `IA_ACCESS_KEY`/`IA_SECRET_KEY` env vars
- Dockerfile: pinned `internetarchive` via build ARG `IA_PYPI_VERSION`; set HOME/XDG_CONFIG_HOME
- README expanded with usage, buildx example, and config docs
