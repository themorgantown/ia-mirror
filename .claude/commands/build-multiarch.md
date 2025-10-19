Build and push multi-architecture Docker image for release: $ARGUMENTS

This creates official release images for both amd64 and arm64 architectures and pushes them to Docker Hub.

⚠️  **IMPORTANT**: Only use this for official releases. Normally, GitHub Actions handles this automatically when you push a version tag.

Prerequisites:
- Docker Buildx setup with multi-platform support
- Authenticated to Docker Hub (`docker login`)
- Version should follow semantic versioning

Follow these steps:

1. Verify you're ready for a multi-arch release
2. Set up buildx builder if needed  
3. Build for multiple platforms
4. Push to Docker Hub registry

Commands to run:

```bash
# Verify buildx is available
docker buildx version

# Create builder if needed (one-time setup)
docker buildx create --name multiarch --use --bootstrap || true

# Build and push multi-arch image
echo "Building ia-mirror:$ARGUMENTS for multiple architectures..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag themorgantown/ia-mirror:$ARGUMENTS \
  --tag themorgantown/ia-mirror:latest \
  --push \
  -f docker/Dockerfile \
  docker

# Verify the push
echo "Verifying multi-arch manifest..."
docker buildx imagetools inspect themorgantown/ia-mirror:$ARGUMENTS
```

This will:
- Build for both amd64 and arm64 platforms
- Tag as both versioned ($ARGUMENTS) and latest
- Push directly to Docker Hub
- Create a multi-arch manifest

**Note**: Prefer using the automated GitHub Actions workflow by pushing a git tag:
```bash
git tag -a v$ARGUMENTS -m "Release v$ARGUMENTS"
git push origin v$ARGUMENTS
```

The automated workflow also pushes to GHCR and handles authentication automatically.