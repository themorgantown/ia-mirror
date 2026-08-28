---
description: Manually build and push a multi-arch release image. Escape hatch only — CI normally does this.
argument-hint: "X.Y.Z  (no v prefix)"
allowed-tools: Bash, Read, Grep, Glob
---

Manually build and push `themorgantown/ia-mirror:$ARGUMENTS` for amd64 and arm64.

**Prefer `/release $ARGUMENTS` instead.** Pushing a git tag triggers
`.github/workflows/release-buildx.yml`, which builds the same image, pushes to
both Docker Hub *and* GHCR, and handles auth. Use this command only when that
workflow is broken or unavailable.

This pushes public images. Confirm with the user before running it.

## Pre-flight

```bash
test "$(cat VERSION)" = "$ARGUMENTS" && echo "VERSION ok" || echo "VERSION MISMATCH"
grep 'ARG IA_PYPI_VERSION\|ARG PROJECT_VERSION' docker/Dockerfile
docker buildx version
docker login
```

Run `/quality-check` first if you have not already.

## Build and push

```bash
docker buildx create --name multiarch --use --bootstrap 2>/dev/null || docker buildx use multiarch

IA_PYPI_VERSION=$(grep -E '^internetarchive==' docker/requirements.txt | cut -d= -f3)

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --no-cache --pull \
  --build-arg "IA_PYPI_VERSION=${IA_PYPI_VERSION}" \
  --build-arg "PROJECT_VERSION=$ARGUMENTS" \
  --tag "themorgantown/ia-mirror:$ARGUMENTS" \
  --tag "themorgantown/ia-mirror:latest" \
  --push \
  -f docker/Dockerfile \
  docker
```

Both `--build-arg`s are required, and this is the reason to be careful here:
`release-buildx.yml` passes them explicitly, so omitting them makes a manual
build diverge from a CI build. `PROJECT_VERSION` becomes the
`org.opencontainers.image.version` label — get it wrong and the published image
is mislabelled, which is exactly what happened to v1.1.5 and v1.1.6.

`--no-cache` keeps the `apk upgrade` layer from shipping already-patched OS
packages into a public image.

`IA_PYPI_VERSION` is read from `requirements.txt` so the two cannot drift.

## Verify

```bash
docker buildx imagetools inspect "themorgantown/ia-mirror:$ARGUMENTS"

for p in linux/amd64 linux/arm64; do
  echo "== $p"
  docker run --rm --platform "$p" -e WEB_ENABLED=false \
    "themorgantown/ia-mirror:$ARGUMENTS" --print-effective-config | head -5
done
```

The manifest must list both platforms, and each must run. `WEB_ENABLED=false` is
required or the container starts Gunicorn and hangs instead of printing config.

## Note

This does **not** push to GHCR — CI does that. After a manual push, the two
registries are out of sync until the next tagged release.
