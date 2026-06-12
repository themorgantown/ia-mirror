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
    chown -R app:app "$CONFIG_DIR"
    chmod 600 "$CONFIG_DIR/ia.ini"
  fi
fi

# Optional: append a custom User-Agent suffix for all IA requests.
# Requires internetarchive >= 5.7.2.
# This preserves the default internetarchive User-Agent (including access key) and appends the suffix.
if [[ -n "${IA_USER_AGENT_SUFFIX:-}" ]]; then
    mkdir -p "$CONFIG_DIR"
    IA_INI="$CONFIG_DIR/ia.ini"

    # Ensure the config exists so we can add the [general] setting.
    if [[ ! -f "$IA_INI" ]]; then
        : > "$IA_INI"
    fi

    # If the file isn't writable (e.g., user mounted it read-only), warn and continue.
    if [[ ! -w "$IA_INI" ]]; then
        echo "Warning: cannot write $IA_INI to set user_agent_suffix (is it mounted read-only?)" >&2
    else
        TMP_INI="$(mktemp)"
        awk -v val="$IA_USER_AGENT_SUFFIX" '
            BEGIN { in_general=0; saw_general=0; wrote=0 }
            /^\[general\][[:space:]]*$/ {
                saw_general=1; in_general=1; print; next
            }
            /^\[[^]]+\]/ {
                if (in_general && !wrote) { print "user_agent_suffix = " val; wrote=1 }
                in_general=0
                print
                next
            }
            {
                if (in_general && $0 ~ /^[[:space:]]*user_agent_suffix[[:space:]]*=/) {
                    print "user_agent_suffix = " val
                    wrote=1
                    next
                }
                print
            }
            END {
                if (in_general && !wrote) {
                    print "user_agent_suffix = " val
                    wrote=1
                }
                if (!saw_general) {
                    print ""
                    print "[general]"
                    print "user_agent_suffix = " val
                }
            }
        ' "$IA_INI" > "$TMP_INI"
        mv "$TMP_INI" "$IA_INI"
        chown -R app:app "$CONFIG_DIR" || true
        chmod 600 "$IA_INI" || true
    fi
fi

# ----------------- PUID/PGID Handling for Unraid/LinuxServer compatibility -----------------
# Default to 1000 if not set
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Modify 'app' user to match requested PUID
if [ "$(id -u app)" != "$PUID" ]; then
    echo "Switching app UID from $(id -u app) to $PUID"
    usermod -o -u "$PUID" app
fi

# Modify 'app' group to match requested PGID
if [ "$(id -g app)" != "$PGID" ]; then
    echo "Switching app GID from $(id -g app) to $PGID"
    groupmod -o -g "$PGID" app
fi
# -------------------------------------------------------------------------------------------

# Fix permissions on /downloads (mounted volume) and /data
# Optimization: Only run chown if the root dir is not owned by app, or if explicitly requested.
# Recursive chown on large directories is extremely slow.
SKIP_PERM_FIX=${SKIP_PERM_FIX:-false}

if [[ "$SKIP_PERM_FIX" != "true" ]]; then
    echo "Checking permissions..."
    mkdir -p /downloads /data
    
    # Check if /downloads is owned by app (uid matches PUID)
    if [[ "$(stat -c '%u' /downloads 2>/dev/null)" != "$PUID" ]]; then
        echo "Fixing permissions for /downloads (recursive)..."
        chown -R app:app /downloads
    else
        echo "/downloads already owned by app. Skipping recursive chown."
    fi

    # Check if /data is owned by app
    if [[ "$(stat -c '%u' /data 2>/dev/null)" != "$PUID" ]]; then
        echo "Fixing permissions for /data (recursive)..."
        chown -R app:app /data
    else
        echo "/data already owned by app. Skipping recursive chown."
    fi
else
    echo "Skipping permission fix as requested."
fi

# Determine if web UI should be enabled
WEB_ENABLED=${WEB_ENABLED:-true}

if [[ "$WEB_ENABLED" == "true" ]]; then
  # Start web UI with Gunicorn (threaded worker for SocketIO)
  WEB_HOST=${WEB_HOST:-127.0.0.1}
  WEB_PORT=${WEB_PORT:-17865}
  WEB_DB_PATH=${WEB_DB_PATH:-/data/ui.db}
  WEB_RUNNER=${WEB_RUNNER:-real}
  
  # Set environment variables for the app to use
  export WEB_DB_PATH WEB_RUNNER
  
  # Ensure database directory exists and is writable by app user
  DB_DIR=$(dirname "$WEB_DB_PATH")
  # We run as root now, so we can make the dir and change owner
  mkdir -p "$DB_DIR"
  chown -R app:app "$DB_DIR"
  
    exec su-exec app gunicorn \
    --bind "${WEB_HOST}:${WEB_PORT}" \
    --worker-class gthread \
    --threads 4 \
    --workers 1 \
    --timeout 600 \
    --access-logfile - \
    --error-logfile - \
    "web.app:app"
else
  # Run CLI fetcher.py (tini is PID 1 and will forward signals)
    exec su-exec app /usr/local/bin/python /app/fetcher.py "$@"
fi
