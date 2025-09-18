#!/usr/bin/env bash
set -euo pipefail

# If IA_ACCESS_KEY and IA_SECRET_KEY are provided via env, create a minimal ia.ini
CONFIG_DIR="/home/app/.config/ia"
if [[ -n "${IA_ACCESS_KEY:-}" && -n "${IA_SECRET_KEY:-}" ]]; then
  echo "Setting up IA authentication configuration..."
  mkdir -p "$CONFIG_DIR"
  if [[ ! -f "$CONFIG_DIR/ia.ini" ]]; then
    echo "Creating IA configuration file at $CONFIG_DIR/ia.ini"
    cat > "$CONFIG_DIR/ia.ini" <<EOF
[DEFAULT]
access = ${IA_ACCESS_KEY}
secret = ${IA_SECRET_KEY}
EOF
    chown -R app:app "$CONFIG_DIR"
    chmod 600 "$CONFIG_DIR/ia.ini"
    echo "IA configuration file created successfully"
  else
    echo "IA configuration file already exists"
  fi
else
  echo "No IA authentication credentials provided"
fi

# Execute fetcher.py (tini is PID 1 and will forward signals)
echo "Starting IA mirror fetcher with arguments: $*"
exec /usr/local/bin/python /app/fetcher.py "$@"
