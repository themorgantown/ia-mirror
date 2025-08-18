#!/usr/bin/env bash
set -euo pipefail

# If IA_ACCESS_KEY and IA_SECRET_KEY are provided via env, create a minimal ia.ini
CONFIG_DIR="/home/app/.config/ia"
if [[ -n "${IA_ACCESS_KEY:-}" && -n "${IA_SECRET_KEY:-}" ]]; then
  mkdir -p "$CONFIG_DIR"
  if [[ ! -f "$CONFIG_DIR/ia.ini" ]]; then
    cat > "$CONFIG_DIR/ia.ini" <<EOF
[DEFAULT]
access = ${IA_ACCESS_KEY}
secret = ${IA_SECRET_KEY}
EOF
    chown -R app:app "$CONFIG_DIR" || true
    chmod 600 "$CONFIG_DIR/ia.ini" || true
  fi
fi

# Execute fetcher.py (tini is PID 1 and will forward signals)
exec /usr/local/bin/python /app/fetcher.py "$@"
