# Development: tags and releases


## How to version
- Use Semantic Versioning: MAJOR.MINOR.PATCH (e.g., 0.1.3)
- Create a Git tag with a leading "v": v0.1.3
  - CI triggers only on tags matching v*.*.*
  - Docker Hub tags will NOT include the "v" (it strips it)
  - GHCR tags will include the "v"

## Typical flow
1) Make and commit your changes
- git checkout main
- git pull --ff-only
- # edit files, then:
- git add -A
- git commit -m "Your message"
- git push origin main

2) Tag the release (note the v-prefix)
- git tag -a v0.1.3 -m "Release v0.1.3"
- git push origin v0.1.3

That’s it. GitHub Actions will build multi-arch images and push:
- Docker Hub: themorgantown/ia-mirror:0.1.3 and :latest
- GHCR: ghcr.io/<owner>/ia-mirror:v0.1.3 and :latest

## Fixing mistakes
- Tagged without the v-prefix (e.g., 0.1.3)? CI won’t run.
  - Remove the bad tag and retag with v:
    - git push origin :refs/tags/0.1.3
    - git tag -d 0.1.3
    - git tag -a v0.1.3 -m "Release v0.1.3"
    - git push origin v0.1.3

- Need to redo a version tag v0.1.3?
  - Delete it, then recreate:
    - git push origin :refs/tags/v0.1.3
    - git tag -d v0.1.3
    - git tag -a v0.1.3 -m "Release v0.1.3"
    - git push origin v0.1.3

## Notes
- The workflow only pushes on v* tags; manual runs won’t push unless a v-tag is present.
- Docker Hub shows non-v tags; GHCR mirrors the v-prefixed tag.
