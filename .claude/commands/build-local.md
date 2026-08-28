---
description: Build the ia-mirror Docker image locally as ia-mirror:local and smoke-test it.
argument-hint: "[--no-cache to force a fresh build]"
allowed-tools: Bash, Read, Grep, Glob
---

Build the local development image. $ARGUMENTS

## Success criteria

- `ia-mirror:local` exists and is newly built.
- The smoke test prints an effective-config JSON block and exits 0.

## Build

```bash
docker build --pull -t ia-mirror:local -f docker/Dockerfile docker
docker images ia-mirror:local
```

Add `--no-cache` when the build is feeding a security scan or a dependency
upgrade. The `apk upgrade` layer caches, so a cached build silently keeps
already-patched OS packages and makes `docker scout` report CVEs that are in fact
already fixed upstream.

## Smoke test

```bash
docker run --rm -e WEB_ENABLED=false ia-mirror:local --print-effective-config
```

`WEB_ENABLED=false` is required. `docker/entrypoint.sh` defaults it to `true`,
and in that mode it `exec`s Gunicorn and discards the CLI arguments entirely — so
without it this command hangs forever instead of printing anything.

To smoke-test the Web UI instead:

```bash
docker run --rm -d --name ia-mirror-smoke -p 17865:17865 -e WEB_HOST=0.0.0.0 ia-mirror:local
sleep 5 && curl -fsS http://localhost:17865/api/status; echo
docker rm -f ia-mirror-smoke
```

`WEB_HOST=0.0.0.0` is required too — the default is `127.0.0.1`, which binds to
the container's own loopback and is unreachable from the host even with `-p`.

## If the build fails

- Confirm the Docker daemon is running: `docker version`.
- Syntax-check first: `python3 -m py_compile docker/fetcher.py docker/web/*.py`.
- Lint the Dockerfile: `docker run --rm -i hadolint/hadolint hadolint --ignore DL3008 - < docker/Dockerfile`.
- The build context is `docker/`, not the repo root — a file outside `docker/`
  cannot be `COPY`d.
