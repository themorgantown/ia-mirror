Create a new release tag and trigger CI/CD pipeline.

Version format: $ARGUMENTS (e.g., 0.1.3)

Follow these steps:

1. Ensure all changes are committed and pushed to main
2. Verify the version follows semantic versioning (MAJOR.MINOR.PATCH)
3. Create an annotated git tag with "v" prefix
4. Push the tag to origin to trigger GitHub Actions
5. Monitor the Actions workflow for build status
6. Verify images are published to both Docker Hub and GHCR

Commands to run:
```bash
git checkout main
git pull --ff-only
git tag -a v$ARGUMENTS -m "Release v$ARGUMENTS"  
git push origin v$ARGUMENTS
```

After tagging:
- Check GitHub Actions: gh run list
- Verify Docker Hub: https://hub.docker.com/r/themorgantown/ia-mirror/tags  
- Verify GHCR: gh api repos/themorgantown/ia-mirror/packages

If you need to fix a bad tag:
```bash
git push origin :refs/tags/v$ARGUMENTS
git tag -d v$ARGUMENTS
git tag -a v$ARGUMENTS -m "Release v$ARGUMENTS"
git push origin v$ARGUMENTS
```