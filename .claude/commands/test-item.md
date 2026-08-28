---
description: Dry-run the container against a real Internet Archive item to verify connectivity, auth, and file selection.
argument-hint: "<archive.org identifier>"
allowed-tools: Bash, Read, Grep, Glob
---

Dry-run ia-mirror against Internet Archive item: **$ARGUMENTS**

Nothing is downloaded — this verifies the container can reach archive.org,
authenticate, and resolve the item's file list.

## Success criteria

- Container exits 0.
- Output lists the files that *would* be downloaded.
- No authentication or network errors in the log.

## Run

```bash
mkdir -p ./test-mirror

docker run --rm \
  -e WEB_ENABLED=false \
  -v "$PWD/test-mirror:/data" \
  -e IA_IDENTIFIER="$ARGUMENTS" \
  -e IA_DESTDIR=/data \
  -e IA_DRY_RUN=true \
  -e IA_ACCESS_KEY="${IA_ACCESS_KEY:-}" \
  -e IA_SECRET_KEY="${IA_SECRET_KEY:-}" \
  ia-mirror:local
```

`WEB_ENABLED=false` is mandatory. `docker/entrypoint.sh` defaults it to `true`,
and in that mode it starts Gunicorn and never invokes `fetcher.py` — the run
hangs and the `IA_*` variables are ignored.

Requires `ia-mirror:local` to exist; build it with `/build-local` first.

## Auth

Credentials are optional for public items. If auth fails, either export
`IA_ACCESS_KEY` / `IA_SECRET_KEY`, or mount an existing config instead:

```bash
-v ~/.config/ia:/home/app/.config/ia:ro
```

## Known-good identifiers

- `jillem-full-archive` — small item
- `opensource` — small collection sample

## Note on `IA_*` variables

On the CLI path these are *defaults only*: an explicitly passed argument always
wins (`--glob '*'` beats `IA_GLOB=*.zip`). In Web UI mode, job-configuring `IA_*`
variables are stripped from the fetcher subprocess entirely — see
`JOB_CONFIG_ENV_VARS` in `docker/web/jobs.py`. So this command only exercises the
CLI path.
