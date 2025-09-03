# Minimal MVP Dockerfile for IA mirror
ARG IA_PYPI_VERSION="5.5.0"
FROM python:3.13.7-slim AS base
ARG IA_PYPI_VERSION

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/app \
    XDG_CONFIG_HOME=/home/app/.config

# Install required system packages (curl for diagnostics if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini bash && \
    rm -rf /var/lib/apt/lists/*

# Add non-root user
RUN useradd -u 1000 -m -d /home/app app
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY fetcher.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# Directories (owned by app)
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app

# Labels for OCI / Docker Hub
LABEL org.opencontainers.image.title="ia-mirror" \
    org.opencontainers.image.description="Minimal Internet Archive download/mirror utility (MVP)" \
    org.opencontainers.image.source="https://github.com/themorgantown/ia-mirror" \
    org.opencontainers.image.documentation="https://github.com/themorgantown/ia-mirror#readme" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.version="${IA_PYPI_VERSION}" \
    org.opencontainers.image.authors="Daniel Morgan"

ENTRYPOINT ["/usr/bin/tini","--","/app/entrypoint.sh"]

HEALTHCHECK --interval=1m --timeout=10s --start-period=1m --retries=3 \
    CMD ["/usr/local/bin/python", "/app/fetcher.py", "--print-effective-config"]