---
description: One-shot release prep — upgrade all dependencies, run the full verification gate, then increment the version and write the CHANGELOG.
argument-hint: "[patch | minor | major | X.Y.Z]  (default: patch)"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

Get this repo to a taggable state in one pass. Version bump: **$ARGUMENTS**
(default `patch` if empty).

Run the three phases below **in order**. Each phase is a gate: if it fails, stop,
report exactly what failed, and do not start the next phase. Do not commit, tag,
or push at any point — this command ends with a clean, verified, uncommitted diff.

## Phase 1 — Upgrade

Follow `.claude/commands/upgrade.md` in full: all four pinned surfaces (PyPI
packages, base image, GitHub Actions SHAs, vendored browser assets).

Gate: every pin is at latest, or you can state why it is held back.

## Phase 2 — Verify

Follow the "Verify" section of `.claude/commands/upgrade.md`.

Gate, all of which must hold:

- fresh `--no-cache --pull` build succeeds
- `pip-audit` clean
- `hadolint` clean
- containerized pytest passes (71 tests)
- `./tests/runtests.sh` passes (21/21)
- `docker scout cves` 0 Critical / 0 High

## Phase 3 — Increment

Follow `.claude/commands/increment-version.md` with the requested bump level.

The CHANGELOG entry must cover the Phase 1 upgrades **and** anything else landed
on `main` since the last tag.

Gate: no file still references the old version, and the built image's
`org.opencontainers.image.version` label reads the new one.

## Report

Finish with a short summary:

- version: old → new
- dependencies moved (name old → new), and pins already current
- verification results, each with its actual number (tests passed, CVE counts)
- anything skipped or held back, and why

Then state the next step explicitly: review the diff, commit, and run `/release
<version>` to tag and trigger CI.
