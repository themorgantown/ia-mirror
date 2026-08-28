---
description: Tag a release and trigger the CI/CD publish pipeline. Run /release-prep first.
argument-hint: "X.Y.Z  (no v prefix)"
allowed-tools: Bash, Read, Grep, Glob
---

Tag and publish release **v$ARGUMENTS**.

This command only tags. The version bump, CHANGELOG, and verification are
`/release-prep`'s job — run that first.

## Pre-flight — all must pass before tagging

A tag is expensive to take back, and a wrong one publishes a mislabelled image.
Check every item, and stop if any fails.

```bash
# 1. VERSION matches the requested tag
test "$(cat VERSION)" = "$ARGUMENTS" && echo "VERSION ok" || echo "VERSION MISMATCH"

# 2. Dockerfile label matches
grep 'ARG PROJECT_VERSION' docker/Dockerfile

# 3. CHANGELOG has an entry
grep -n "^## \[$ARGUMENTS\]" CHANGELOG.md

# 4. Working tree is clean and pushed
git status --porcelain
git log origin/main..HEAD --oneline

# 5. Tag is not already taken
git tag -l "v$ARGUMENTS"

# 6. CI is green on the commit being tagged
gh run list --branch main --limit 3
```

Confirm with the user before tagging — this publishes to Docker Hub and GHCR.

## Tag

```bash
git checkout main
git pull --ff-only
git tag -a "v$ARGUMENTS" -m "Release v$ARGUMENTS"
git push origin "v$ARGUMENTS"
```

The `v` prefix is required — `.github/workflows/release-buildx.yml` gates on
`startsWith(github.ref, 'refs/tags/v')` and will not build without it.

## Verify the publish

```bash
gh run list --workflow=release-buildx.yml --limit 3
gh run watch
```

Then confirm the multi-arch manifest and the version label landed:

```bash
docker buildx imagetools inspect "themorgantown/ia-mirror:$ARGUMENTS"
```

- Docker Hub: https://hub.docker.com/r/themorgantown/ia-mirror/tags
- GHCR: `gh api /users/themorgantown/packages/container/ia-mirror/versions --jq '.[0:3][].metadata.container.tags'`

## Retracting a bad tag

Only do this if the tag has not been consumed downstream, and confirm with the
user first — it rewrites a published ref.

```bash
git push origin ":refs/tags/v$ARGUMENTS"
git tag -d "v$ARGUMENTS"
# fix the problem, commit, push, then re-tag
```

Images already pushed to Docker Hub and GHCR are **not** removed by deleting the
tag. Prefer bumping to the next patch version over reusing one.
