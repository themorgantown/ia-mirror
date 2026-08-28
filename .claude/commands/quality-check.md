---
description: Full verification gate — syntax, lint, dependency audit, unit tests, integration suite, and CVE scan.
allowed-tools: Bash, Read, Grep, Glob
---

Run the complete quality gate. This is the same gate `/release-prep` uses before
a version bump.

## Success criteria

Report each with its actual number. Do not report a pass for a step you skipped.

| Check | Expected |
|---|---|
| Python syntax | compiles clean |
| Dockerfile lint | no hadolint findings |
| Dependency audit | no known vulnerabilities |
| Containerized pytest | 71 passed |
| Integration suite | 21/21 passed |
| Docker Scout | 0 Critical / 0 High |

## Run

```bash
# 1. Python syntax
python3 -m py_compile docker/fetcher.py docker/web/app.py docker/web/routes.py \
  docker/web/jobs.py scripts/check_dependencies.py

# 2. Dockerfile lint
docker run --rm -i hadolint/hadolint hadolint --ignore DL3008 - < docker/Dockerfile

# 3. Dependency audit
#    pip was removed from the image in v1.1.7 (its vendored SBOM was the last
#    source of High CVEs), so this cannot run in the container — audit the
#    requirements file from an isolated host venv, same as ci.yml does.
python3 -m venv .audit-venv && .audit-venv/bin/pip install --quiet --upgrade pip-audit
.audit-venv/bin/pip-audit -r docker/requirements.txt

# 4. Build fresh. --no-cache matters: the `apk upgrade` layer caches, and a stale
#    layer makes step 6 report OS CVEs that upstream has already fixed.
docker build --no-cache --pull -t ia-mirror:local -f docker/Dockerfile docker

# 5a. Unit tests — run these in the container, not on the host.
docker run --rm --entrypoint /bin/sh -v "$PWD/tests:/app/tests:ro" -w /app ia-mirror:local \
  -lc "pytest tests/test_fetcher.py tests/test_web_backend.py tests/test_watcher_service.py tests/test_file_browser_api.py -q"

# 5b. Full integration suite (builds its own test image, runs real downloads)
./tests/runtests.sh

# 6. Vulnerability scan
docker scout cves --only-severity critical,high local://ia-mirror:local
```

## Why the tests run in the container

Running `pytest` against the host Python fails two tests on macOS, and both are
fixture artifacts rather than product bugs:

- `test_destinations_validate_valid` posts `/downloads`, which only exists
  inside the image.
- `test_list_files_subdir` — the fixture patches `web.routes.os.path.abspath` to
  redirect `/downloads` at a `tempfile.mkdtemp()` directory, but `safe_join()` in
  `docker/web/parsing.py` calls `os.path.realpath`, which is not patched. On
  macOS `/var` is a symlink to `/private/var`, so the resolved child path no
  longer sits under the unresolved base and every entry is filtered out.

Both pass on Linux, where the temp path has no symlink to resolve. The container
is the supported test environment.

## If Scout reports fixable OS CVEs

Check whether the fix is actually published for the pinned Alpine branch before
changing anything:

```bash
docker run --rm --entrypoint sh ia-mirror:local -lc "apk update -q; apk policy <package>"
```

If `apk policy` lists the fixed version, the image was built from a stale cache —
rebuild with `--no-cache`. If it does not, the fix is not out yet: bump the
Alpine minor in `docker/Dockerfile` or record the exposure in the CHANGELOG.
