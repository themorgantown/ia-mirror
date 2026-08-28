---
description: Upgrade every pinned dependency (PyPI, base image, GitHub Actions, vendored JS) to latest, then verify the image still builds and passes.
argument-hint: "[optional: a single package to upgrade, e.g. gunicorn]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

Upgrade the pinned dependencies of this repo to their latest releases and prove
nothing broke. $ARGUMENTS

## Success criteria

Done when **all** of the following hold. Do not report success on a partial pass —
say which step failed and stop.

- Every pin below is at its latest release, or you state why it was held back.
- `docker build --no-cache --pull` succeeds.
- `pip-audit` reports no known vulnerabilities.
- `hadolint` reports no findings.
- Containerized pytest passes (currently 71 tests).
- `./tests/runtests.sh` passes (currently 21/21).
- `docker scout cves` reports 0 Critical / 0 High.
- CHANGELOG.md has an entry describing every version that moved.

## The four pinned surfaces

Nothing here updates itself. Check all four.

### 1. Python packages — `docker/requirements.txt`

```bash
for p in $(sed 's/==.*//' docker/requirements.txt); do
  echo "$p installed=$(grep -i "^$p==" docker/requirements.txt | cut -d= -f3) \
latest=$(curl -s "https://pypi.org/pypi/$p/json" | python3 -c 'import sys,json;print(json.load(sys.stdin)["info"]["version"])')"
done
```

`internetarchive` is pinned in **three** places that must move together:
`docker/requirements.txt`, `ARG IA_PYPI_VERSION` in `docker/Dockerfile`, the
`IA_PYPI_VERSION=` build-arg in `.github/workflows/release-buildx.yml`, and the
`--build-arg IA_PYPI_VERSION=` example in `README.md`. Grep for the old version
string to be sure you caught every one.

### 2. Base image — `FROM` in `docker/Dockerfile`

```bash
curl -s "https://hub.docker.com/v2/repositories/library/python/tags/?page_size=100&name=alpine" \
  | python3 -c "
import sys,json,re
names=[t['name'] for t in json.load(sys.stdin)['results']]
print('\n'.join(sorted(n for n in names if re.match(r'^3\.\d+\.\d+-alpine3\.\d+$', n))[-10:]))"
```

Prefer the highest Python patch on the highest Alpine minor. Alpine minor bumps
are how OS-level CVEs (openssl, sqlite-libs) actually get fixed — see the v1.1.7
CHANGELOG entry.

### 3. GitHub Actions — `.github/workflows/*.yml`

Actions are pinned to **immutable commit SHAs** with a trailing `# vX.Y.Z`
comment. Keep that convention; never replace a SHA with a bare tag.

```bash
for r in $(grep -rhoE 'uses: [^@]+' .github/workflows/ | sed 's/uses: //' | sort -u); do
  echo "$r -> $(gh api repos/$r/releases/latest --jq .tag_name 2>/dev/null)"
done
```

To repin one:

```bash
gh api repos/OWNER/REPO/commits/vX.Y.Z --jq .sha
```

Read the release notes for any **major** bump before taking it — check whether an
input the workflow uses was removed.

### 4. Vendored browser assets — `docker/static/vendor/`

Versions are in the file header comments.

```bash
head -c 200 docker/static/vendor/socket.io.min.js
grep -oE 'Bootstrap v[0-9.]+' docker/static/vendor/bootstrap.bundle.min.js
echo "socket.io-client latest: $(curl -s https://registry.npmjs.org/socket.io-client/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"])')"
echo "bootstrap latest:        $(curl -s https://registry.npmjs.org/bootstrap/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"])')"
```

Keep the `socket.io-client` major in step with the `python-socketio` server pin.
Re-download from cdnjs and confirm the header version in the fetched file matches
what you asked for.

## Verify

Run in order. Stop at the first failure.

```bash
# Build fresh. --no-cache is NOT optional: the `apk upgrade` layer caches, and a
# cached layer will keep shipping already-patched OS CVEs and make Scout lie.
docker build --no-cache --pull -t ia-mirror:local -f docker/Dockerfile docker

python3 -m venv .audit-venv && .audit-venv/bin/pip install --quiet --upgrade pip-audit
.audit-venv/bin/pip-audit -r docker/requirements.txt

docker run --rm -i hadolint/hadolint hadolint --ignore DL3008 - < docker/Dockerfile

python3 -m py_compile docker/fetcher.py docker/web/app.py docker/web/routes.py docker/web/jobs.py

docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/app/tests:ro" -w /app ia-mirror:local \
  -lc "pytest tests/test_fetcher.py tests/test_web_backend.py tests/test_watcher_service.py tests/test_file_browser_api.py -q"

./tests/runtests.sh

docker scout cves --only-severity critical,high local://ia-mirror:local
```

If Scout reports fixable OS CVEs, first confirm the fix is actually published for
the pinned Alpine branch before assuming the image is at fault:

```bash
docker run --rm --entrypoint sh ia-mirror:local -lc "apk update -q; apk policy <package>"
```

If `apk policy` shows the fixed version available, your build used a stale cache —
rebuild with `--no-cache`. If it does not, the fix is not released yet; bump the
Alpine minor or record the exposure in the CHANGELOG.

## Record

Add or extend the top CHANGELOG.md section with a `### Dependencies` list
(`name old → new`) and a `### CI/CD` list for action bumps. Follow the existing
style: say *why* a bump matters when it fixes something, and explicitly name the
pins you checked that were already current.

Do not commit, tag, or push. Stop here and report.
