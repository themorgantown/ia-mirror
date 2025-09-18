# ia-mirror 🚀 (Dockerized Internet Archive Mirror Utility)

[![Docker Pulls](https://img.shields.io/docker/pulls/themorgantown/ia-mirror)](https://hub.docker.com/r/themorgantown/ia-mirror)

**Easily mirror and download Internet Archive collections with a single Docker command!**

Minimal Dockerized MVP for mirroring/downloading items from the Internet Archive using the `ia` CLI (via the `internetarchive` Python package).

This repository provides a small container that wraps parallel downloads, resume, dry-run, checksum verification and a small summary `report.json` written alongside the downloaded data. Useful for large, resumed, or complex downloads where dockerization makes life easier.

> **Note**: This is a small change to test the Docker Hub README sync workflow.

## 📚 Table of Contents
- [✨ Features](#-features)
- [⚡ QuickStart](#-quickstart)
- [📘 Examples](#-examples)
- [⚙️ Configuration](#️-configuration)
- [📂 Files & Outputs](#-files--outputs)
- [💻 Development](#-development)
- [🛡️ Security](#️-security)
- [🤝 Contributing](#-contributing)
- [🔧 Troubleshooting](#-troubleshooting)

## ✨ Features

- 🐳 **Dockerized** - Run anywhere with Docker
- ⚡ **Parallel Downloads** - Speed up downloads with concurrent connections
- 🔄 **Resume Support** - Automatically resume interrupted downloads
- 🔍 **Dry Run Mode** - Test before downloading
- ✅ **Checksum Verification** - Verify file integrity
- 📊 **Status Tracking** - Monitor download progress with `report.json`
- 🔐 **Secure Authentication** - Multiple auth methods supported
- 🎯 **File Selection** - Download specific files with glob patterns

## ⚡ QuickStart

### 1. Pull the Image
```bash
docker pull themorgantown/ia-mirror:latest
```

### 2. Run a Basic Download
```bash
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=The_Babe_Ruth_Collection \
  -e IA_DESTDIR=/data \
  -e IA_ACCESS_KEY=your_access_key_here \
  -e IA_SECRET_KEY=your_secret_key_here \
  themorgantown/ia-mirror:latest
```

### 3. Get Your Credentials
- Create an account at [https://archive.org/account/login](https://archive.org/account/login)
- Generate or locate your access key and secret at [https://archive.org/account/s3.php](https://archive.org/account/s3.php)

### 4. Authentication Methods

**Host Config Method** (Quick but exposes host config):
```bash
ia configure  # Creates ~/.config/ia/ia.ini
# Then run container mounting it read-only:
-v "$HOME/.config/ia:/home/app/.config/ia:ro"
```

**Environment Variables Method** (Recommended for ephemeral creds):
```bash
-e IA_ACCESS_KEY=XXXX -e IA_SECRET_KEY=YYYY
# The entrypoint writes /home/app/.config/ia/ia.ini at runtime
```

**Docker Secrets Method** (Recommended for production):
Store ia.ini contents in a secret, mount it, then copy to `/home/app/.config/ia/ia.ini`

### 5. Verify Inside Container (Optional)
```bash
docker exec -it <container> ia whoami
```

> No credentials needed for public-item dry runs (use `--dry-run`).

## 📘 Examples

### Basic Download
```bash
docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" themorgantown/ia-mirror:latest
```

### With More Parallel Downloads
```bash
docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" -e IA_CONCURRENCY="10" themorgantown/ia-mirror:latest
```

### Dry Run (No Download)
```bash
docker run --rm -v $(pwd)/mirror:/data -e IA_IDENTIFIER="The_Babe_Ruth_Collection" -e IA_DESTDIR="/data" -e IA_DRY_RUN="true" themorgantown/ia-mirror:latest
```

### Verify Only (No Downloads)
```bash
docker run --rm -v $(pwd)/mirror:/data themorgantown/ia-mirror:latest The_Babe_Ruth_Collection --destdir /data --verify-only
```

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `IA_IDENTIFIER` | Item or collection identifier | ✅ |
| `IA_DESTDIR` | Destination directory under /data | |
| `IA_CONCURRENCY` | Parallel workers (default 5) | |
| `IA_CHECKSUM` | Enable checksums | |
| `IA_DRY_RUN` | Dry-run mode | |
| `IA_VERIFY_ONLY` | Verify existing files only | |
| `IA_COLLECTION` | Collection mode | |
| `IA_GLOB` | File filter pattern | |
| `IA_MAX_MBPS` | Bandwidth limit | |
| `IA_LOG_LEVEL` | Logging level (INFO/DEBUG/ERROR) | |
| `IA_ACCESS_KEY` | Archive.org access key | ✅ (if no config) |
| `IA_SECRET_KEY` | Archive.org secret key | ✅ (if no config) |

### File Selection with Glob Patterns

By default, the tool downloads ALL files for an item. You can filter files with glob patterns:

```bash
IA_GLOB=*.zip            # Only ZIP archives
IA_GLOB=*.{mp3,flac}     # Audio originals
IA_GLOB=*2024*           # Files containing 2024
IA_GLOB=*/scans/*        # Files in scans/ subdirectory
```

> Leave `IA_GLOB` unset (or use `-g '*'`) to mirror everything.

## 📂 Files & Outputs

- **Download destination**: `/data/<identifier>` (unless `--destdir` provided)
- **Status file**: `/data/<identifier>/.ia_status/<identifier>.json`
- **Snapshot report**: `/data/<identifier>/report.json`
- **Log file**: `/data/<identifier>/ia_download.log` (also streamed to stdout)

## 💻 Development

### Build Local Image
```bash
# Single-arch build
docker build -t themorgantown/ia-mirror:0.2.0 -f docker/Dockerfile docker
docker build --pull --rm -f docker/Dockerfile -t ia-mirror:local docker

# Multi-arch build (recommended for publishing)
docker buildx create --use --name ia-builder || true
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg IA_PYPI_VERSION=5.5.0 \
  -t themorgantown/ia-mirror:0.2.0 --push -f docker/Dockerfile docker
```

### Development Usage Examples

**Dry-run (no credentials needed for public items)**:
```bash
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  themorgantown/ia-mirror:latest --dry-run
```

**With host `ia` config**:
```bash
docker run --rm \
  -v "$HOME/.config/ia:/home/app/.config/ia:ro" \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_CONCURRENCY=6 \
  -e IA_CHECKSUM=1 \
  themorgantown/ia-mirror:latest
```

**With environment credentials**:
```bash
docker run --rm \
  -v "$PWD/mirror:/data" \
  -e IA_IDENTIFIER=jillem-full-archive \
  -e IA_ACCESS_KEY=AKXXX -e IA_SECRET_KEY=SKYYY \
  -e IA_CONCURRENCY=6 \
  themorgantown/ia-mirror:latest
```

> **Production recommendation**: Use Docker secrets or your orchestration's secret mechanism and inject into the container as env vars or bind a single secret file as `/run/secrets/ia.ini` then copy into `/home/app/.config/ia/ia.ini` at startup.

### Versioning & Releases

We use Semantic Versioning (MAJOR.MINOR.PATCH) for releases:

1. **Tag the release** (note the v-prefix):
   ```bash
   git tag -a v0.2.0 -m "Release v0.2.0"
   git push origin v0.2.0
   ```

2. **GitHub Actions** will automatically build multi-arch images and push to:
   - Docker Hub: `themorgantown/ia-mirror:0.2.0` and `:latest`
   - GHCR: `ghcr.io/<owner>/ia-mirror:v0.2.0` and `:latest`

3. **Fixing mistakes**:
   - If you tagged without the v-prefix, remove and retag:
     ```bash
     git push origin :refs/tags/0.2.0
     git tag -d 0.2.0
     git tag -a v0.2.0 -m "Release v0.2.0"
     git push origin v0.2.0
     ```

## 🛡️ Security

- **Never bake credentials into images**; use tokens/secrets instead
- **Entry point writes `ia.ini` from env/secrets**; never echo secret contents
- **Sensitive files (ia.ini) use restrictive permissions** (600)
- **Use Docker secrets method for production** deployments
- **Regularly rotate your Archive.org access keys**

## 🤝 Contributing

We welcome contributions! Here's how you can help:

1. **Fork the repository**
2. **Create a feature branch** (`git checkout -b feature/AmazingFeature`)
3. **Commit your changes** (`git commit -m 'Add some AmazingFeature'`)
4. **Push to the branch** (`git push origin feature/AmazingFeature`)
5. **Open a Pull Request**

### Development Guidelines

- Follow the project's coding standards
- Write clear commit messages
- Add tests for new functionality
- Ensure all tests pass before submitting a PR
- Update documentation when changing functionality

### Reporting Issues

- Use the GitHub issue tracker to report bugs
- Don't hammer the IA!
- Include detailed steps to reproduce
- Specify your environment (Docker version, OS, etc.)
- Include relevant logs or error messages

## 🔧 Troubleshooting

- **If the image cannot find `ia`**: Ensure the `internetarchive` package version installed in the image provides the `ia` CLI (we pin with `IA_PYPI_VERSION` build arg). You can also bind a local `ia` binary into `/app/ia`.

- **Permission errors**: The container runs as `app:1000`; ensure volume write access.

- **Authentication issues**: 
  - Verify your access/secret keys at [https://archive.org/account/s3.php](https://archive.org/account/s3.php)
  - Check that `ia.ini` has restrictive permissions (600)

- **Download failures**: 
  - Try with `IA_DRY_RUN=true` to test without downloading
  - Check the log file at `/data/<identifier>/ia_download.log`
  - Verify the item identifier exists on archive.org

- **Performance issues**: 
  - Adjust `IA_CONCURRENCY` (default 5) for your network
  - Use `IA_MAX_MBPS` to limit bandwidth usage
  - Consider using `IA_GLOB` to download only needed files