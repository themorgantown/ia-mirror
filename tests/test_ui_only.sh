#!/usr/bin/env bash
# Run only the UI test

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

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
NC='\033[0m'

# Setup output directory
OUTPUT_DIR="$SCRIPT_DIR/test_output"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR_HOST="$(host_path "$OUTPUT_DIR")"

# Docker image name
IMAGE="${IMAGE:-ia-mirror:test}"

# Wrap docker for Git Bash
docker() {
    if [ "$IS_GITBASH" -eq 1 ]; then
        MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" command docker "$@"
    else
        command docker "$@"
    fi
}

# Build test image
echo -e "Building test image..."
docker build -t "$IMAGE" -f "$REPO_ROOT_HOST/docker/Dockerfile" "$REPO_ROOT_HOST/docker/" > "$OUTPUT_DIR/build.log" 2>&1 || {
    echo -e "${RED}Build failed. See $OUTPUT_DIR/build.log${NC}"
    exit 1
}

# UI Test function
test_ui() {
    local ID="test_ui"
    local DESC="Web UI Integration Tests"
    local CONTAINER_NAME="ia-mirror-test-ui"

    # Cleanup previous
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    # Start container
    docker run -d --name "$CONTAINER_NAME" \
        -p 17866:17865 \
        -v "$OUTPUT_DIR_HOST:/downloads" \
        -e WEB_ENABLED=true \
        -e WEB_PORT=17865 \
        -e WEB_RUNNER=mock \
        "$IMAGE" > "$OUTPUT_DIR/$ID.start.log" 2>&1

    # Wait for start
    local READY=0
    for i in {1..30}; do
        if curl -s http://localhost:17866/ > /dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 1
    done

    if [ $READY -eq 0 ]; then
        echo -e "${RED}✗ [$ID] FAIL${NC}: $DESC - Container failed to start"
        docker logs "$CONTAINER_NAME" > "$OUTPUT_DIR/$ID.docker.log" 2>&1
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
        return 1
    fi

    local FAILED=0
    local LOG="$OUTPUT_DIR/$ID.log"
    echo "Starting UI checks..." > "$LOG"

    # 1. HTML check
    if ! curl -s http://localhost:17866/ | grep -q "ia-mirror"; then
        echo "FAIL: HTML check" >> "$LOG"
        FAILED=1
    fi

    # 2. Config Endpoint
    if ! curl -s http://localhost:17866/api/config | grep -q "destination"; then
        echo "FAIL: Config endpoint" >> "$LOG"
        FAILED=1
    fi

    # 3. Add to Queue (Using 1 item to be faster)
    RESPONSE=$(curl -s -X POST http://localhost:17866/api/queue/add \
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

    # 4. Check Queue Length
    STATUS=$(curl -s http://localhost:17866/api/status)
    QUEUE_LEN=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
    if [ "$QUEUE_LEN" -lt 1 ]; then
        echo "FAIL: Queue length expected >= 1, got $QUEUE_LEN" >> "$LOG"
        FAILED=1
    fi

    # 4b. Test Removal
    JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_ids'][0])" 2>/dev/null || echo 0)
    REMOVE_RESP=$(curl -s -X DELETE "http://localhost:17866/api/queue/$JOB_ID")
    STATUS_AFTER=$(curl -s http://localhost:17866/api/status)
    Q_LEN_AFTER=$(echo "$STATUS_AFTER" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
    if [ "$Q_LEN_AFTER" -ne 0 ]; then
        echo "FAIL: Queue item was not removed. Q_LEN: $Q_LEN_AFTER" >> "$LOG"
        FAILED=1
    fi

    # 4c. Add back for subsequent tests
    curl -s -X POST http://localhost:17866/api/queue/add \
        -H "Content-Type: application/json" \
        -d '{
            "text": "item1",
            "operation": "download",
            "config": {"destination": "/downloads", "concurrency": 4}
        }' > /dev/null

    # 5. Start Job (Mock)
    START_RESP=$(curl -s -X POST http://localhost:17866/api/job/start)
    if ! echo "$START_RESP" | grep -q "started"; then
        echo "FAIL: Start job. Response: $START_RESP" >> "$LOG"
        FAILED=1
    fi

    # 6. Wait for completion (Mock runner is fast, but give it time)
    local COMPLETED=0
    for i in {1..60}; do
        sleep 1
        STATUS=$(curl -s http://localhost:17866/api/status)
        Q_LEN=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))" 2>/dev/null || echo 0)
        if [ "$Q_LEN" -eq 0 ]; then
            COMPLETED=1
            break
        fi
    done
    if [ $COMPLETED -eq 0 ]; then
        echo "FAIL: Jobs did not complete in time" >> "$LOG"
        FAILED=1
    fi

    # 7. Check History
    JOBS=$(curl -s http://localhost:17866/api/jobs)
    count=$(echo "$JOBS" | python3 -c "import sys, json; print(len([j for j in json.load(sys.stdin)['jobs'] if j['status'] in ['completed', 'failed']]))" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
        echo "FAIL: No completed jobs in history" >> "$LOG"
        FAILED=1
    fi

    # Cleanup
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ [$ID] PASS${NC}: $DESC"
        return 0
    else
        echo -e "${RED}✗ [$ID] FAIL${NC}: $DESC - See $LOG"
        return 1
    fi
}

# Run the UI test
echo "Running UI test..."
if test_ui; then
    echo -e "${GREEN}UI test passed!${NC}"
    exit 0
else
    echo -e "${RED}UI test failed!${NC}"
    exit 1
fi