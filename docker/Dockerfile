# Production image for ia-mirror
ARG IA_PYPI_VERSION="5.8.0"
ARG PROJECT_VERSION="1.0.3"
FROM python:3.14.5-alpine3.23 AS base
ARG IA_PYPI_VERSION
ARG PROJECT_VERSION

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/app \
    XDG_CONFIG_HOME=/home/app/.config

# Install required system packages.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache && \
    apk add --no-cache \
        bash \
        ca-certificates \
        coreutils \
        curl \
        shadow \
        su-exec \
        tini

# Add non-root user
RUN groupadd -g 1000 app && useradd -u 1000 -g app -m -d /home/app app
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY fetcher.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# Copy the rest of the application
COPY web /app/web
COPY templates /app/templates
COPY static /app/static

# Create directories and set permissions
RUN mkdir -p /downloads /data && chown app:app /downloads /data

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:17865/api/status || exit 1

# Expose port
EXPOSE 17865

# Labels for OCI / Docker Hub
LABEL org.opencontainers.image.title="ia-mirror" \
    org.opencontainers.image.description="Internet Archive download and mirroring utility with Web UI and CLI modes" \
    org.opencontainers.image.source="https://github.com/themorgantown/ia-mirror" \
    org.opencontainers.image.documentation="https://github.com/themorgantown/ia-mirror#readme" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.version="${PROJECT_VERSION}" \
    org.opencontainers.image.authors="Daniel Morgan"

ENTRYPOINT ["/sbin/tini","--","/app/entrypoint.sh"]
