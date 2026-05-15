#!/usr/bin/env bash
# Consolidated test runner for ia-mirror.
# Runs Python tests in Docker, then CLI integration tests, then Web UI integration tests.

set -u

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

# Git Bash/MSYS on Windows will rewrite POSIX-looking paths (like /app) into
# Windows host paths (like C:/Program Files/Git/app), which breaks Docker args
# such as -w /app and volume destinations like :/downloads.
UNAME_S="$(uname -s 2>/dev/null || true)"
IS_GITBASH=0
if [ -n "${MSYSTEM:-}" ] || [[ "$UNAME_S" == MINGW* ]] || [[ "$UNAME_S" == MSYS* ]]; then
    IS_GITBASH=1
fi

host_path() {
    if [ "$IS_GITBASH" -eq 1 ] && command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$1"
    else
        echo "$1"
    fi
}

SCRIPT_DIR_HOST="$(host_path "$SCRIPT_DIR")"
REPO_ROOT_HOST="$(host_path "$REPO_ROOT")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Setup output directory
OUTPUT_DIR="$SCRIPT_DIR/test_output"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR_HOST="$(host_path "$OUTPUT_DIR")"

# Prefer python3 when available; fall back to python.
PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

# Docker image name
IMAGE="${IMAGE:-ia-mirror:test}"

# Wrap docker so container paths (/app, /downloads, etc) are not MSYS-converted.
docker() {
    if [ "$IS_GITBASH" -eq 1 ]; then
        MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" command docker "$@"
    else
        command docker "$@"
    fi
}

# timeout executes docker as a subprocess and bypasses the bash docker() wrapper.
# Wrap timeout so Docker still receives unmodified container paths on Git Bash.
timeout() {
    local seconds="$1"
    shift

    local timeout_cmd=""
    if command -v timeout >/dev/null 2>&1; then
        timeout_cmd="timeout"
    elif command -v gtimeout >/dev/null 2>&1; then
        timeout_cmd="gtimeout"
    fi

    if [ -z "$timeout_cmd" ]; then
        if command -v python3 >/dev/null 2>&1; then
            python3 - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

secs = int(float(sys.argv[1]))
cmd = sys.argv[2:]

try:
    # Start in its own process group so we can terminate the full tree.
    proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
    proc.wait(timeout=secs)
    sys.exit(proc.returncode)
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    sys.exit(124)
PY
        elif command -v python >/dev/null 2>&1; then
            python - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

secs = int(float(sys.argv[1]))
cmd = sys.argv[2:]

try:
    proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
    proc.wait(timeout=secs)
    sys.exit(proc.returncode)
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    sys.exit(124)
PY
        else
            echo "WARN: No timeout/gtimeout/python available; running without timeout: $*" >&2
            "$@"
        fi
        return
    fi

    if [ "$IS_GITBASH" -eq 1 ] && [ "${1:-}" = "docker" ]; then
        MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" command "$timeout_cmd" "$seconds" "$@"
    else
        command "$timeout_cmd" "$seconds" "$@"
    fi
}

# -----------------------------------------------------------------------------
# 1. Build Docker Image
# -----------------------------------------------------------------------------

# Auth args
AUTH_ARGS=""
if [ -f "$REPO_ROOT/docker/live.env" ]; then
    # Extract keys
    ACCESS_KEY=$(grep "^IA_ACCESS_KEY=" "$REPO_ROOT/docker/live.env" | cut -d= -f2 | tr -d "\"'")
    SECRET_KEY=$(grep "^IA_SECRET_KEY=" "$REPO_ROOT/docker/live.env" | cut -d= -f2 | tr -d "\"'")
    if [ -n "$ACCESS_KEY" ] && [ -n "$SECRET_KEY" ]; then
        AUTH_ARGS="-e IA_ACCESS_KEY=$ACCESS_KEY -e IA_SECRET_KEY=$SECRET_KEY"
    fi
fi

echo -e "${BLUE}Building test image...${NC}"
docker build -t "$IMAGE" -f "$REPO_ROOT_HOST/docker/Dockerfile" "$REPO_ROOT_HOST/docker/" > "$OUTPUT_DIR/build.log" 2>&1 || {
    echo -e "${RED}Build failed. See $OUTPUT_DIR/build.log${NC}"
    exit 1
}

# -----------------------------------------------------------------------------
# 2. Run Python Tests (inside Docker)
# -----------------------------------------------------------------------------
echo -e "${BLUE}Running Python tests...${NC}"
if docker run --rm --entrypoint "" \
    -v "$SCRIPT_DIR_HOST:/app/tests" \
    -w /app \
    "$IMAGE" pytest /app/tests > "$OUTPUT_DIR/python_tests.log" 2>&1; then
    echo -e "${GREEN}✓ Python Tests PASS${NC}"
else
    echo -e "${RED}✗ Python Tests FAIL${NC}"
    head -n 20 "$OUTPUT_DIR/python_tests.log"
    echo "..."
    tail -n 10 "$OUTPUT_DIR/python_tests.log"
    exit 1
fi

# Helper to record result
# Usage: record_result <test_id> <status> <message>
record_result() {
    echo "$2|$3" > "$OUTPUT_DIR/$1.result"
    if [ "$2" == "PASS" ]; then
        echo -e "${GREEN}✓ [$1] PASS${NC}: $3"
    else
        echo -e "${RED}✗ [$1] FAIL${NC}: $3"
        # Print tail of log if exists
        if [ -f "$OUTPUT_DIR/$1.log" ]; then
            echo -e "${YELLOW}--- Log tail ($1) ---${NC}"
            tail -n 5 "$OUTPUT_DIR/$1.log"
            echo -e "${YELLOW}---------------------${NC}"
        fi
    fi
}

# --- Test Definitions ---

test_00_invalid_config() {
    local ID="test00"
    local DESC="Invalid configuration (missing identifier)"
    # Must set WEB_ENABLED=false to force CLI mode, otherwise it starts web server and hangs
    if docker run --rm -e WEB_ENABLED=false $AUTH_ARGS "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1; then
        record_result "$ID" "FAIL" "$DESC - Should have failed"
    else
        if grep -q "identifier required" "$OUTPUT_DIR/$ID.log"; then
            record_result "$ID" "PASS" "$DESC"
        else
            record_result "$ID" "FAIL" "$DESC - Unexpected error message"
        fi
    fi
}

test_01_dry_run() {
    local ID="test01"
    local DESC="Dry-run for public item"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"
    
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log" && \
       [ -f "$TEST_DIR/listofearlyameri00fren/report.json" ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_02_estimate() {
    local ID="test02"
    local DESC="Estimate-only"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_ESTIMATE_ONLY=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Estimate Summary" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_03_glob() {
    local ID="test03"
    local DESC="Dry-run with glob filter"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_GLOB="*.xml" \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_04_exclude() {
    local ID="test04"
    local DESC="Dry-run with exclude filter"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_EXCLUDE="*.txt,*.xml" \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_05_format() {
    local ID="test05"
    local DESC="Dry-run with format filter"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_FORMAT="pdf" \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_06_config() {
    local ID="test06"
    local DESC="Print effective config"
    timeout 30 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -e IA_IDENTIFIER=test_item \
        -e IA_CONCURRENCY=8 \
        -e IA_CHECKSUM=1 \
        "$IMAGE" --print-effective-config > "$OUTPUT_DIR/$ID.json" 2>&1 || true
    
    # Clean json
    sed -n '/^{/,/^}/p' "$OUTPUT_DIR/$ID.json" > "$OUTPUT_DIR/${ID}_clean.json" || true
    
    if grep -q '"concurrency": 8' "$OUTPUT_DIR/${ID}_clean.json"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_07_batch() {
    local ID="test07"
    local DESC="Batch mode dry-run"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/data" \
        -v "${SCRIPT_DIR_HOST}/batch_source.csv:/app/batch_source.csv:ro" \
        -e IA_DRY_RUN=1 \
        "$IMAGE" --use-batch-source --batch-source-path /app/batch_source.csv > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Batch mode: processing 2 rows" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_09_lock() {
    local ID="test09"
    local DESC="Lockfile behavior"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 30 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=locktest \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if [ ! -f "$TEST_DIR/locktest/.ia_status/lock.json" ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC - Lockfile not cleaned up"
    fi
}

test_10_backoff() {
    local ID="test10"
    local DESC="Backoff config"
    # No volume needed for config print
    timeout 30 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -e IA_IDENTIFIER=test_item \
        -e IA_BACKOFF_BASE=5 \
        "$IMAGE" --print-effective-config > "$OUTPUT_DIR/$ID.json" 2>&1 || true
    
    if grep -q '"base": 5.0' "$OUTPUT_DIR/$ID.json"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_16_source() {
    local ID="test16"
    local DESC="Source filtering"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_SOURCE=metadata \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_17_ignore() {
    local ID="test17"
    local DESC="Ignore existing"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR/synth_psr60"
    echo "fake" > "$TEST_DIR/synth_psr60/dummy_ignore.xml"
    # Pretend this is a real file
    local TARGET="$TEST_DIR/synth_psr60/synth_psr60_meta.xml"
    echo "fake content" > "$TARGET"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_IGNORE_EXISTING=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "fake content" "$TARGET"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_18_nolock() {
    local ID="test18"
    local DESC="No lock mode"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR/synth_psr60/.ia_status"
    echo '{"pid": 99999}' > "$TEST_DIR/synth_psr60/.ia_status/lock.json"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_NO_LOCK=1 \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Dry-run simulation" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_19_forcemeta() {
    local ID="test19"
    local DESC="Force metadata update"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR/synth_psr60/.ia_status"
    echo "corrupt" > "$TEST_DIR/synth_psr60/.ia_status/metadata_synth_psr60.json"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_FORCE_METADATA_UPDATE=1 \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "{" "$TEST_DIR/synth_psr60/.ia_status/metadata_synth_psr60.json"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_20_resumefolders() {
    local ID="test20"
    local DESC="Resume folders"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR/synth_psr60/psr60"
    
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_RESUMEFOLDERS=1 \
        -e IA_DRY_RUN=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep "SKIP:" "$OUTPUT_DIR/$ID.log" | grep -q "psr60.zip"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

rerun_missing_parallel_results() {
    local missing=()
    local expected=(
        test00 test01 test02 test03 test04 test05 test06 test07
        test09 test10 test16 test17 test18 test19 test20
    )

    for id in "${expected[@]}"; do
        if [ ! -f "$OUTPUT_DIR/$id.result" ]; then
            missing+=("$id")
        fi
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        return
    fi

    echo -e "${YELLOW}Missing parallel result files: ${missing[*]}. Re-running those tests sequentially for reliable accounting.${NC}"

    for id in "${missing[@]}"; do
        case "$id" in
            test00) test_00_invalid_config ;;
            test01) test_01_dry_run ;;
            test02) test_02_estimate ;;
            test03) test_03_glob ;;
            test04) test_04_exclude ;;
            test05) test_05_format ;;
            test06) test_06_config ;;
            test07) test_07_batch ;;
            test09) test_09_lock ;;
            test10) test_10_backoff ;;
            test16) test_16_source ;;
            test17) test_17_ignore ;;
            test18) test_18_nolock ;;
            test19) test_19_forcemeta ;;
            test20) test_20_resumefolders ;;
        esac
    done
}

# --- Real Download Tests (Sequential) ---

test_08_download_small() {
    local ID="test08"
    local DESC="Actual download (small)"
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$OUTPUT_DIR_HOST:/downloads" \
        -e IA_IDENTIFIER=listofearlyameri00fren \
        -e IA_GLOB="*_meta.xml" \
        -e IA_CONCURRENCY=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if [ -s "$OUTPUT_DIR/listofearlyameri00fren/listofearlyameri00fren_meta.xml" ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_12_throttle() {
    local ID="test12"
    local DESC="Bandwidth throttling"
    # Use a smaller item or just check if it starts and limits
    # AdventuresOfBabeRuth is 80MB, might be too big for quick test.
    # Let's use synth_psr60 which is small, but set limit very low (1Mbps)
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$OUTPUT_DIR_HOST:/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_MAX_MBPS=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if [ -f "$OUTPUT_DIR/synth_psr60/synth_psr60_meta.xml" ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_13_sync() {
    local ID="test13"
    local DESC="Sync mode (delete orphan)"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"
    
    # Use synth_psr60 which is smaller/more reliable
    mkdir -p "$TEST_DIR/synth_psr60"
    touch "$TEST_DIR/synth_psr60/orphan_file.txt"
    
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_SYNC=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if [ ! -f "$TEST_DIR/synth_psr60/orphan_file.txt" ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

test_14_checksum() {
    local ID="test14"
    local DESC="Checksum verification"
    local TEST_DIR="$OUTPUT_DIR/checksum_test"
    mkdir -p "$TEST_DIR"
    
    # First download clean
    timeout 120 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        "$IMAGE" > /dev/null 2>&1 || true
        
    local TARGET="$TEST_DIR/synth_psr60/synth_psr60_meta.xml"
    if [ -f "$TARGET" ]; then
        # Corrupt it
        echo "garbage" > "$TARGET"
        
        timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
            -v "$(host_path "$TEST_DIR"):/downloads" \
            -e IA_IDENTIFIER=synth_psr60 \
            -e IA_CHECKSUM=1 \
            "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true
            
        if grep -q "<?xml" "$TARGET"; then
            record_result "$ID" "PASS" "$DESC"
        else
            record_result "$ID" "FAIL" "$DESC - File not restored"
        fi
    else
        record_result "$ID" "FAIL" "$DESC - Setup failed"
    fi
}

test_15_verify() {
    local ID="test15"
    local DESC="Verify only"
    local TEST_DIR="$OUTPUT_DIR/$ID"
    mkdir -p "$TEST_DIR"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$(host_path "$TEST_DIR"):/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_VERIFY_ONLY=1 \
        "$IMAGE" > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Verify only mode" "$OUTPUT_DIR/$ID.log" || grep -q "Verified .* OK" "$OUTPUT_DIR/$ID.log"; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC"
    fi
}

# --- UI Tests ---

run_web_ui_integration_test() {
    local ID="test_ui"
    local DESC="Web UI Integration Tests"
    local CONTAINER_NAME="ia-mirror-test-ui-suite"
    local TEST_PORT=17866
    local FAILED=0
    local LOG="$OUTPUT_DIR/$ID.log"

    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    docker run -d --name "$CONTAINER_NAME" \
        -p "$TEST_PORT:17865" \
        -v "$OUTPUT_DIR_HOST:/downloads" \
        -e WEB_ENABLED=true \
        -e WEB_PORT=17865 \
        -e WEB_RUNNER=mock \
        "$IMAGE" > "$OUTPUT_DIR/$ID.start.log" 2>&1

    local READY=0
    for _ in {1..30}; do
        if curl -s "http://localhost:$TEST_PORT/" >/dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 1
    done

    if [ "$READY" -eq 0 ]; then
        docker logs "$CONTAINER_NAME" > "$OUTPUT_DIR/$ID.docker.log" 2>&1 || true
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
        record_result "$ID" "FAIL" "$DESC - Container failed to start"
        return
    fi

    echo "Starting UI checks..." > "$LOG"

    if ! curl -s "http://localhost:$TEST_PORT/" | grep -q "ia-mirror"; then
        echo "FAIL: HTML check" >> "$LOG"
        FAILED=1
    fi

    if ! curl -s "http://localhost:$TEST_PORT/api/config" | grep -q "destination"; then
        echo "FAIL: Config endpoint" >> "$LOG"
        FAILED=1
    fi

    local RESPONSE
    RESPONSE=$(curl -s -X POST "http://localhost:$TEST_PORT/api/queue/add" \
        -H "Content-Type: application/json" \
        -d '{
            "text": "item1",
            "operation": "download",
            "config": {"destination": "/downloads", "concurrency": 4}
        }')
    if ! echo "$RESPONSE" | grep -q '"valid_count":1'; then
        echo "FAIL: Queue add. Response: $RESPONSE" >> "$LOG"
        FAILED=1
    fi

    local STATUS QUEUE_LEN
    STATUS=$(curl -s "http://localhost:$TEST_PORT/api/status")
    QUEUE_LEN=$(echo "$STATUS" | "$PYTHON_BIN" -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
    if [ "$QUEUE_LEN" -lt 1 ]; then
        echo "FAIL: Queue length expected >= 1, got $QUEUE_LEN" >> "$LOG"
        FAILED=1
    fi

    local JOB_ID REMOVE_RESP STATUS_AFTER Q_LEN_AFTER
    JOB_ID=$(echo "$RESPONSE" | "$PYTHON_BIN" -c "import sys, json; print(json.load(sys.stdin)['job_ids'][0])" 2>/dev/null || echo 0)
    REMOVE_RESP=$(curl -s -X DELETE "http://localhost:$TEST_PORT/api/queue/$JOB_ID")
    STATUS_AFTER=$(curl -s "http://localhost:$TEST_PORT/api/status")
    Q_LEN_AFTER=$(echo "$STATUS_AFTER" | "$PYTHON_BIN" -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
    if [ "$Q_LEN_AFTER" -ne 0 ]; then
        echo "FAIL: Queue item was not removed. Response: $REMOVE_RESP" >> "$LOG"
        FAILED=1
    fi

    curl -s -X POST "http://localhost:$TEST_PORT/api/queue/add" \
        -H "Content-Type: application/json" \
        -d '{
            "text": "item1",
            "operation": "download",
            "config": {"destination": "/downloads", "concurrency": 4}
        }' > /dev/null

    local START_RESP
    START_RESP=$(curl -s -X POST "http://localhost:$TEST_PORT/api/job/start")
    if ! echo "$START_RESP" | grep -q "started"; then
        echo "FAIL: Start job. Response: $START_RESP" >> "$LOG"
        FAILED=1
    fi

    local COMPLETED=0 JOBS HISTORY_COUNT
    for _ in {1..60}; do
        sleep 1
        STATUS=$(curl -s "http://localhost:$TEST_PORT/api/status")
        QUEUE_LEN=$(echo "$STATUS" | "$PYTHON_BIN" -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
        if [ "$QUEUE_LEN" -eq 0 ]; then
            COMPLETED=1
            break
        fi
    done
    if [ "$COMPLETED" -eq 0 ]; then
        echo "FAIL: Jobs did not complete in time" >> "$LOG"
        FAILED=1
    fi

    JOBS=$(curl -s "http://localhost:$TEST_PORT/api/jobs")
    HISTORY_COUNT=$(echo "$JOBS" | "$PYTHON_BIN" -c "import sys, json; print(len([j for j in json.load(sys.stdin)['jobs'] if j['status'] in ['completed', 'failed']]))" 2>/dev/null || echo 0)
    if [ "$HISTORY_COUNT" -eq 0 ]; then
        echo "FAIL: No completed jobs in history" >> "$LOG"
        FAILED=1
    fi

    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    if [ "$FAILED" -eq 0 ]; then
        record_result "$ID" "PASS" "$DESC"
    else
        record_result "$ID" "FAIL" "$DESC - See $LOG"
    fi
}

# --- Execution ---

echo -e "${BLUE}Running Dry-Run Tests (Parallel)...${NC}"
test_00_invalid_config &
test_01_dry_run &
test_02_estimate &
test_03_glob &
test_04_exclude &
test_05_format &
test_06_config &
test_07_batch &
test_09_lock &
test_10_backoff &
test_16_source &
test_17_ignore &
test_18_nolock &
test_19_forcemeta &
test_20_resumefolders &

wait
rerun_missing_parallel_results

echo -e "${BLUE}Running Download Tests (Sequential)...${NC}"
test_08_download_small
test_12_throttle
test_13_sync
test_14_checksum
test_15_verify

echo -e "${BLUE}Running UI Tests...${NC}"
run_web_ui_integration_test

# --- Summary ---
PASSED=$(cat "$OUTPUT_DIR"/*.result 2>/dev/null | grep -c "^PASS" || true)
FAILED=$(cat "$OUTPUT_DIR"/*.result 2>/dev/null | grep -c "^FAIL" || true)
TOTAL=$((PASSED + FAILED))

echo ""
echo "======================================"
echo "  Test Summary"
echo "======================================"
echo "Total: $TOTAL"
echo -e "${GREEN}Passed: $PASSED${NC}"
if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Failed: $FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
