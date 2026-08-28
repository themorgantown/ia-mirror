---
description: Bump the project version consistently across VERSION, Dockerfile, and CHANGELOG, then verify no file was missed.
argument-hint: "patch | minor | major | X.Y.Z"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

Increment the project version to: **$ARGUMENTS**

If `$ARGUMENTS` is `patch`, `minor`, or `major`, compute the new version from the
current `VERSION` file. If it is an explicit `X.Y.Z`, use it verbatim. If it is
empty, default to `patch`.

## Success criteria

- The new version appears in **every** location listed below, and the old version
  appears in none of them.
- The CHANGELOG has a new top section for it, dated today.
- `docker build` succeeds and the built image's
  `org.opencontainers.image.version` label reads the new version.
- Nothing is committed, tagged, or pushed.

## Locations the version lives in

Version drift here has shipped wrong image labels before (v1.1.5 and v1.1.6 both
published with a stale `1.1.4` label — see the v1.1.7 CHANGELOG "Fixed" entry).
Treat this list as the checklist, and confirm with a grep rather than by memory.

| File | What to change |
|---|---|
| `VERSION` | The whole file. No trailing newline — match the existing format. |
| `docker/Dockerfile` | `ARG PROJECT_VERSION="X.Y.Z"` |
| `CHANGELOG.md` | New `## [X.Y.Z] - YYYY-MM-DD` section at the top |

The git tag is `vX.Y.Z` (v-prefixed) but `VERSION` and `PROJECT_VERSION` are not.
`release-buildx.yml` derives the unprefixed form itself; do not add a `v` there.

## Steps

1. Read the current version and derive the new one.

   ```bash
   cat VERSION
   ```

2. Apply the change to `VERSION` and `docker/Dockerfile`.

3. Write the CHANGELOG section. Cover everything on `main` since the last tag —
   do not invent entries, and do not leave real changes out:

   ```bash
   git log "$(git describe --tags --abbrev=0)"..HEAD --oneline
   git diff "$(git describe --tags --abbrev=0)"..HEAD --stat
   ```

   Use the existing heading vocabulary: `### Security`, `### Dependencies`,
   `### CI/CD`, `### Fixed`, `### Added`. Match the house style — entries explain
   the user-visible consequence, not just the diff.

4. Verify nothing was missed.

   ```bash
   OLD=<old version>; NEW=<new version>
   echo "--- stale references to $OLD ---"
   grep -rn --fixed-strings "$OLD" VERSION docker/Dockerfile README.md .github/workflows/ \
     | grep -v CHANGELOG || echo "none"
   echo "--- new version present ---"
   grep -n --fixed-strings "$NEW" VERSION docker/Dockerfile
   grep -n "^## \[$NEW\]" CHANGELOG.md
   ```

   Historical CHANGELOG entries legitimately mention old versions — ignore those.
   Anything else still on the old version is a miss.

5. Confirm the label actually lands in the image.

   ```bash
   docker build -t ia-mirror:local -f docker/Dockerfile docker
   docker image inspect ia-mirror:local \
     --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'
   ```

   This must print the new version. If it prints the old one, `ARG
   PROJECT_VERSION` was not updated.

6. Sanity-check CHANGELOG ordering — headings must descend by version:

   ```bash
   grep -n "^## \[" CHANGELOG.md | head -12
   ```

Stop here and report. Committing and tagging is `/release`.
