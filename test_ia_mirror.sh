#!/usr/bin/env bash
# Integration tests for ia-mirror
# Tests dry-run, estimate-only, and simulated downloads with public IA items

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Docker image to test (override with IMAGE env var)
IMAGE="${IMAGE:-ia-mirror:test}"

# Build the test image if it doesn't exist or if BUILD=1
if [ "${BUILD:-1}" = "1" ]; then
    echo "Building test image from local Dockerfile..."
    docker build -t ia-mirror:test -f docker/Dockerfile docker/ || {
        echo "Failed to build Docker image"
        exit 1
    }
fi

# Cleanup function (only called at end)
cleanup() {
    if [ "${CLEANUP:-1}" = "1" ]; then
        echo -e "\n${YELLOW}Cleaning up test artifacts...${NC}"
        rm -rf ./test_output
    else
        echo -e "\n${YELLOW}Test artifacts preserved in ./test_output${NC}"
    fi
}

# Helper functions
pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((TESTS_PASSED+=1))
}

fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((TESTS_FAILED+=1))
}

run_test() {
    ((TESTS_RUN+=1))
    echo -e "\n${YELLOW}[Test $TESTS_RUN]${NC} $1"
}

# Create test output directory
mkdir -p ./test_output

echo "======================================"
echo "  ia-mirror Integration Test Suite"
echo "======================================"
echo "Image: $IMAGE"
echo "Date: $(date)"
echo ""

# Test 1: Dry-run for a public item
run_test "Dry-run for public item (no credentials required)"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test1.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test1.log && \
   [ -f ./test_output/listofearlyameri00fren/report.json ]; then
    pass "Dry-run completed and report.json created"
    # Verify report structure
    if jq -e '.status == "dry-run"' ./test_output/listofearlyameri00fren/report.json > /dev/null 2>&1; then
        pass "Report contains correct status: dry-run"
    else
        fail "Report missing or incorrect status"
    fi
else
    fail "Dry-run did not complete successfully"
fi

# Test 2: Estimate-only for a public item
run_test "Estimate-only for public item"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_ESTIMATE_ONLY=1 \
    "$IMAGE" > ./test_output/test2.log 2>&1 || true

if grep -q "Estimate Summary" ./test_output/test2.log && \
   [ -f ./test_output/listofearlyameri00fren/report.json ]; then
    pass "Estimate-only completed and report.json created"
    # Verify report structure
    if jq -e '.status == "estimate-only" and .known_size_bytes' ./test_output/listofearlyameri00fren/report.json > /dev/null 2>&1; then
        pass "Report contains correct status and size estimate"
    else
        fail "Report missing required fields"
    fi
else
    fail "Estimate-only did not complete successfully"
fi

# Test 3: Download with glob filter (single file dry-run)
run_test "Dry-run with glob filter"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_GLOB="*.xml" \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test3.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test3.log; then
    pass "Glob-filtered dry-run completed"
else
    fail "Glob-filtered dry-run failed"
fi

# Test 4: Exclude filter test
run_test "Dry-run with exclude filter"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_EXCLUDE="*.txt,*.xml" \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test4.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test4.log; then
    pass "Exclude-filtered dry-run completed"
else
    fail "Exclude-filtered dry-run failed"
fi

# Test 5: Format filter test
run_test "Dry-run with format filter"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_FORMAT="pdf" \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test5.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test5.log; then
    pass "Format-filtered dry-run completed"
else
    fail "Format-filtered dry-run failed"
fi

# Test 6: Print effective config
run_test "Print effective configuration"
timeout 30 docker run --rm \
    -e IA_IDENTIFIER=test_item \
    -e IA_CONCURRENCY=8 \
    -e IA_CHECKSUM=1 \
    "$IMAGE" --print-effective-config > ./test_output/test6.json 2>&1 || true

if jq -e '.identifier == "test_item" and .concurrency == 8 and .checksum == true' ./test_output/test6.json > /dev/null 2>&1; then
    pass "Effective config printed and parsed correctly"
else
    fail "Effective config missing or incorrect"
fi

# Test 7: Batch mode dry-run
run_test "Batch mode dry-run with CSV"
cat > ./test_output/test_batch.csv <<EOF
source,destdir
listofearlyameri00fren,/data/batch_test_1
EOF

timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -v "$(pwd)/test_output/test_batch.csv:/app/batch_source.csv:ro" \
    -e IA_DRY_RUN=1 \
    "$IMAGE" --use-batch-source --batch-source-path /app/batch_source.csv > ./test_output/test7.log 2>&1 || true

if grep -q "Batch mode: processing 1 rows" ./test_output/test7.log && \
   grep -q "Batch 1/1:" ./test_output/test7.log; then
    pass "Batch mode dry-run completed"
else
    fail "Batch mode dry-run failed"
fi

# Test 8: Actual small download (1 small file)
run_test "Actual download of single XML file"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=listofearlyameri00fren \
    -e IA_GLOB="*_meta.xml" \
    -e IA_CONCURRENCY=1 \
    "$IMAGE" > ./test_output/test8.log 2>&1 || true

# Check if at least the metadata file was attempted/downloaded
if [ -f ./test_output/listofearlyameri00fren/*_meta.xml ] 2>/dev/null || \
   grep -q "already have" ./test_output/test8.log; then
    pass "Small file download completed or file already present"
else
    fail "Small file download failed"
fi

# Test 9: Verify lockfile creation and cleanup
run_test "Lockfile behavior"
timeout 30 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=locktest \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test9.log 2>&1 || true

# Lockfile should be cleaned up after run
if [ ! -f ./test_output/locktest/.ia_status/lock.json ]; then
    pass "Lockfile cleaned up after successful run"
else
    fail "Lockfile was not cleaned up"
fi

# Test 10: Backoff configuration
run_test "Backoff configuration settings"
timeout 30 docker run --rm \
    -e IA_IDENTIFIER=test_item \
    -e IA_BACKOFF_BASE=5 \
    -e IA_BACKOFF_MAX=120 \
    "$IMAGE" --print-effective-config > ./test_output/test10.json 2>&1 || true

if jq -e '.backoff.base == 5 and .backoff.max == 120 and .backoff.enabled == true' ./test_output/test10.json > /dev/null 2>&1; then
    pass "Backoff configuration parsed correctly"
else
    fail "Backoff configuration incorrect"
fi

echo "======================================"
echo "  Test Summary"
echo "======================================"
echo "Total tests run: $TESTS_RUN"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
    cleanup
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    cleanup
    exit 0
fi
