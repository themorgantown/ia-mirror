#!/usr/bin/env bash
# Combined Integration Tests for ia-mirror (CLI + UI + Unit)
# Runs unit tests first, then dry-run tests in parallel, then download tests, then UI integration tests.

set -u

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$SCRIPT_DIR"

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

# Docker image name
IMAGE="${IMAGE:-ia-mirror:test}"

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
docker build -t "$IMAGE" -f "$REPO_ROOT/docker/Dockerfile" "$REPO_ROOT/docker/" > "$OUTPUT_DIR/build.log" 2>&1 || {
    echo -e "${RED}Build failed. See $OUTPUT_DIR/build.log${NC}"
    exit 1
}

# -----------------------------------------------------------------------------
# 2. Run Python Unit Tests (inside Docker)
# -----------------------------------------------------------------------------
echo -e "${BLUE}Running Python Unit Tests...${NC}"
# Use a custom entrypoint or just override it to run pytest
# We mount the tests directory into /app/tests so the container can access test_ui.py
# We run from /app so that 'web' package is importable (it exists at /app/web in the image)
if docker run --rm --entrypoint "" \
    -v "$SCRIPT_DIR":/app/tests \
    -w /app \
    "$IMAGE" pytest /app/tests/test_ui.py > "$OUTPUT_DIR/unit_tests.log" 2>&1; then
    echo -e "${GREEN}✓ Unit Tests PASS${NC}"
else
    echo -e "${RED}✗ Unit Tests FAIL${NC}"
    head -n 20 "$OUTPUT_DIR/unit_tests.log"
    echo "..."
    tail -n 10 "$OUTPUT_DIR/unit_tests.log"
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
    echo "source,destdir" > "$TEST_DIR/batch.csv"
    echo "listofearlyameri00fren,/downloads/batch_test_1" >> "$TEST_DIR/batch.csv"

    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$TEST_DIR:/downloads" \
        -v "$TEST_DIR/batch.csv:/app/batch_source.csv:ro" \
        -e IA_DRY_RUN=1 \
        "$IMAGE" --use-batch-source --batch-source-path /app/batch_source.csv > "$OUTPUT_DIR/$ID.log" 2>&1 || true

    if grep -q "Batch mode: processing 1 rows" "$OUTPUT_DIR/$ID.log"; then
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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

# --- Real Download Tests (Sequential) ---

test_08_download_small() {
    local ID="test08"
    local DESC="Actual download (small)"
    timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$OUTPUT_DIR:/downloads" \
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
        -v "$OUTPUT_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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
    docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
        -v "$TEST_DIR:/downloads" \
        -e IA_IDENTIFIER=synth_psr60 \
        "$IMAGE" > /dev/null 2>&1 || true
        
    local TARGET="$TEST_DIR/synth_psr60/synth_psr60_meta.xml"
    if [ -f "$TARGET" ]; then
        # Corrupt it
        echo "garbage" > "$TARGET"
        
        timeout 60 docker run --rm -e WEB_ENABLED=false $AUTH_ARGS \
            -v "$TEST_DIR:/downloads" \
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
        -v "$TEST_DIR:/downloads" \
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

test_ui() {
    local ID="test_ui"
    local DESC="Web UI Integration Tests"
    local CONTAINER_NAME="ia-mirror-test-ui"
    
    # Cleanup previous
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    
    # Start container
    docker run -d --name "$CONTAINER_NAME" \
        -p 17866:17865 \
        -v "$OUTPUT_DIR:/downloads" \
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
        record_result "$ID" "FAIL" "$DESC - Container failed to start"
        docker logs "$CONTAINER_NAME" > "$OUTPUT_DIR/$ID.docker.log" 2>&1
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
        return
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
    # Using python to parse json safely for test
    QUEUE_LEN=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))")
    if [ "$QUEUE_LEN" -lt 1 ]; then
        echo "FAIL: Queue length expected >= 1, got $QUEUE_LEN" >> "$LOG"
        FAILED=1
    fi

    # 4b. Test Removal
    JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_ids'][0])")
    REMOVE_RESP=$(curl -s -X DELETE "http://localhost:17866/api/queue/$JOB_ID")
    STATUS_AFTER=$(curl -s http://localhost:17866/api/status)
    Q_LEN_AFTER=$(echo "$STATUS_AFTER" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))")
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
        Q_LEN=$(echo "$STATUS" | python3 -c "import sys, json; print(json.load(sys.stdin).get('queue_length', 0))")
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
    count=$(echo "$JOBS" | python3 -c "import sys, json; print(len([j for j in json.load(sys.stdin)['jobs'] if j['status'] in ['completed', 'failed']]))")
    if [ "$count" -eq 0 ]; then
        echo "FAIL: No completed jobs in history" >> "$LOG"
        FAILED=1
    fi

    # Cleanup
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
    
    if [ $FAILED -eq 0 ]; then
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

echo -e "${BLUE}Running Download Tests (Sequential)...${NC}"
test_08_download_small
test_12_throttle
test_13_sync
test_14_checksum
test_15_verify

echo -e "${BLUE}Running UI Tests...${NC}"
test_ui

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
