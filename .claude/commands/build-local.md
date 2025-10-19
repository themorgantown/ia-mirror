Build the ia-mirror Docker image locally.

Follow these steps:

1. Change to the project root directory
2. Build the Docker image using the Dockerfile in docker/ directory
3. Tag it as `ia-mirror:local`
4. Verify the build completed successfully by running `docker images | grep ia-mirror`
5. Optionally test the image with: `docker run --rm ia-mirror:local --print-effective-config`

Use the command:
```bash
docker build -t ia-mirror:local -f docker/Dockerfile docker
```

If you encounter issues:
- Check Docker daemon is running
- Verify you're in the project root
- Check for syntax errors in docker/fetcher.py first
- Review Dockerfile for any issues