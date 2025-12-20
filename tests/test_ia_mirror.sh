#!/usr/bin/env bash
# Integration tests for ia-mirror
# Tests dry-run, estimate-only, and simulated downloads with public IA items

set -euo pipefail

# Get script directory and repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to script directory to keep output local to tests/
cd "$SCRIPT_DIR"

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
    echo "Building test image from local Dockerfile (no-cache)..."
    docker build --no-cache -t ia-mirror:test -f "$REPO_ROOT/docker/Dockerfile" "$REPO_ROOT/docker/" || {
        echo "Failed to build Docker image"
        exit 1
    }
fi

# Cleanup function
cleanup() {
    local exit_code=$?
    if [ "${CLEANUP:-1}" = "1" ]; then
        if [ $exit_code -eq 0 ]; then
            echo -e "\n${YELLOW}Cleaning up test artifacts...${NC}"
            rm -rf ./test_output
        else
            echo -e "\n${YELLOW}Tests failed (exit $exit_code). Preserving artifacts in ./test_output for debugging.${NC}"
        fi
    else
        echo -e "\n${YELLOW}Test artifacts preserved in ./test_output${NC}"
    fi
}

trap cleanup EXIT

# Helper functions
pass() {
    echo -e "${GREEN}✓ PASS${NC}: $1"
    ((TESTS_PASSED+=1))
}

fail() {
    echo -e "${RED}✗ FAIL${NC}: $1"
    ((TESTS_FAILED+=1))
    if [ -f "./test_output/test${TESTS_RUN}.log" ]; then
        echo -e "${YELLOW}--- Last 10 lines of log ---${NC}"
        tail -n 10 "./test_output/test${TESTS_RUN}.log"
        echo -e "${YELLOW}---------------------------${NC}"
    fi
}

run_test() {
    ((TESTS_RUN+=1))
    echo -e "\n${YELLOW}[Test $TESTS_RUN]${NC} $1"
}

# Clean previous test output
rm -rf ./test_output

# Create test output directory
mkdir -p ./test_output

echo "======================================"
echo "  ia-mirror Integration Test Suite"
echo "======================================"
echo "Image: $IMAGE"
echo "Date: $(date)"
echo ""

# Test 0: Invalid configuration (missing identifier)
run_test "Invalid configuration (missing identifier)"
if docker run --rm "$IMAGE" > ./test_output/test0.log 2>&1; then
    fail "Container should have failed without identifier"
else
    if grep -q "identifier required" ./test_output/test0.log; then
        pass "Container failed with correct error message"
    else
        fail "Container failed but error message was unexpected"
    fi
fi

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

# Strip non-JSON lines (like logfile path or health server messages)
sed -n '/^{/,/^}/p' ./test_output/test6.json > ./test_output/test6_clean.json || true

if jq -e '.identifier == "test_item" and .concurrency == 8 and .checksum == true' ./test_output/test6_clean.json > /dev/null 2>&1; then
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
META_FILE=$(ls ./test_output/listofearlyameri00fren/*_meta.xml 2>/dev/null || true)
if [ -n "$META_FILE" ] && [ -s "$META_FILE" ]; then
    pass "Small file download completed and file is not empty"
elif grep -q "already have" ./test_output/test8.log; then
    pass "File already present (skipped download)"
else
    fail "Small file download failed or file is empty"
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

# Strip non-JSON lines
sed -n '/^{/,/^}/p' ./test_output/test10.json > ./test_output/test10_clean.json || true

if jq -e '.backoff.base == 5 and .backoff.max == 120 and .backoff.enabled == true' ./test_output/test10_clean.json > /dev/null 2>&1; then
    pass "Backoff configuration parsed correctly"
else
    fail "Backoff configuration incorrect"
fi

# Test 11: Docker Scout (optional)
# if command -v docker-scout >/dev/null 2>&1 || docker scout version >/dev/null 2>&1; then
#     run_test "Docker Scout quickview"
#     if docker scout quickview "$IMAGE" > ./test_output/test11.log 2>&1; then
#         pass "Docker Scout quickview completed"
#     else
#         fail "Docker Scout quickview failed"
#     fi
# else
#     echo -e "\n${YELLOW}[Test 11]${NC} Skipping Docker Scout (not installed)"
# fi

# Test 12: Bandwidth Throttling (Actual Download)
# Item: AdventuresOfBabeRuth (~80MB)
run_test "Bandwidth Throttling (Actual Download - AdventuresOfBabeRuth)"
# Limit to 50Mbps. 80MB = 640Mb. @50Mbps = ~13s.
timeout 300 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=AdventuresOfBabeRuth \
    -e IA_MAX_MBPS=50 \
    "$IMAGE" > ./test_output/test12.log 2>&1 || true

if [ -f ./test_output/AdventuresOfBabeRuth/AdventuresOfBabeRuth_meta.xml ]; then
    pass "Bandwidth throttled download completed"
else
    fail "Bandwidth throttled download failed"
fi

# Test 13: Sync Mode (using AdventuresOfBabeRuth)
run_test "Sync Mode (Delete orphan)"
# Create orphan
touch ./test_output/AdventuresOfBabeRuth/orphan_file.txt
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=AdventuresOfBabeRuth \
    -e IA_SYNC=1 \
    "$IMAGE" > ./test_output/test13.log 2>&1 || true

if [ ! -f ./test_output/AdventuresOfBabeRuth/orphan_file.txt ]; then
    pass "Orphan file deleted in sync mode"
else
    fail "Orphan file was not deleted"
fi

# Test 14: Checksum Verification (using synth_psr60)
run_test "Checksum Verification (Corrupt file detection)"
# First download clean
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    "$IMAGE" > ./test_output/test14_setup.log 2>&1 || true

# Corrupt a file (keep size same if possible, or just change content)
TARGET_FILE=$(find ./test_output/synth_psr60 -name "*_meta.xml" | head -n1)
if [ -n "$TARGET_FILE" ]; then
    # Read size
    SIZE=$(wc -c < "$TARGET_FILE")
    # Overwrite with random data of SAME size
    head -c "$SIZE" /dev/urandom > "$TARGET_FILE"
    
    timeout 60 docker run --rm \
        -v "$(pwd)/test_output:/data" \
        -e IA_IDENTIFIER=synth_psr60 \
        -e IA_CHECKSUM=1 \
        "$IMAGE" > ./test_output/test14.log 2>&1 || true
        
    # It should have re-downloaded the file (restoring it).
    # We check if the file is valid XML again (it was random garbage)
    if grep -q "<?xml" "$TARGET_FILE"; then
        pass "Checksum mismatch detected and file restored"
    else
        fail "Checksum mismatch not detected or file not restored"
    fi
else
    fail "Could not find file to corrupt for checksum test"
fi

# Test 15: Verify Only
run_test "Verify Only Mode"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_VERIFY_ONLY=1 \
    "$IMAGE" > ./test_output/test15.log 2>&1 || true

if grep -q "Verify only mode" ./test_output/test15.log || grep -q "Summary" ./test_output/test15.log; then
    pass "Verify only run completed"
else
    fail "Verify only run failed"
fi

# Test 16: Source Filtering
run_test "Source Filtering (metadata only)"
timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_SOURCE=metadata \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test16.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test16.log; then
    pass "Source filtering dry-run completed"
else
    fail "Source filtering failed"
fi

# Test 17: Ignore Existing
run_test "Ignore Existing (Skip check)"
# Create a dummy file with WRONG content/size
echo "fake" > ./test_output/synth_psr60/dummy_ignore.xml
# We need to pretend this file is one of the real files.
# We'll use the meta.xml again.
TARGET_FILE=$(find ./test_output/synth_psr60 -name "*_meta.xml" | head -n1)
echo "fake content" > "$TARGET_FILE"

timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_IGNORE_EXISTING=1 \
    "$IMAGE" > ./test_output/test17.log 2>&1 || true

# Content should still be "fake content"
if grep -q "fake content" "$TARGET_FILE"; then
    pass "Existing file ignored (not overwritten)"
else
    fail "Existing file was overwritten"
fi

# Test 18: No Lock
run_test "No Lock Mode"
mkdir -p ./test_output/synth_psr60/.ia_status
echo '{"pid": 99999}' > ./test_output/synth_psr60/.ia_status/lock.json

timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_NO_LOCK=1 \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test18.log 2>&1 || true

if grep -q "Dry-run simulation" ./test_output/test18.log; then
    pass "Ran successfully ignoring lockfile"
else
    fail "Failed to run with lockfile present"
fi

# Cleanup lockfile from Test 18 so it doesn't affect subsequent tests
rm -f ./test_output/synth_psr60/.ia_status/lock.json

# Test 19: Force Metadata Update
run_test "Force Metadata Update"
# Corrupt metadata
echo "corrupt" > ./test_output/synth_psr60/.ia_status/metadata_synth_psr60.json

timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_FORCE_METADATA_UPDATE=1 \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test19.log 2>&1 || true

# Check if metadata is valid JSON now
if jq . ./test_output/synth_psr60/.ia_status/metadata_synth_psr60.json >/dev/null 2>&1; then
    pass "Metadata file refreshed and valid"
else
    fail "Metadata file remains corrupt"
fi

# Test 20: Resume Folders
run_test "Resume Folders (Skip zip if folder exists)"
# synth_psr60 has psr60.zip
# Create folder psr60
mkdir -p ./test_output/synth_psr60/psr60

timeout 60 docker run --rm \
    -v "$(pwd)/test_output:/data" \
    -e IA_IDENTIFIER=synth_psr60 \
    -e IA_RESUMEFOLDERS=1 \
    -e IA_DRY_RUN=1 \
    "$IMAGE" > ./test_output/test20.log 2>&1 || true

# Check if psr60.zip is skipped.
if ! grep -q "psr60.zip" ./test_output/test20.log; then
    pass "Zip file skipped because folder exists"
else
    # Use a more flexible grep to handle potential whitespace/formatting differences
    if grep "SKIP:" ./test_output/test20.log | grep -q "psr60.zip"; then
        pass "Zip file explicitly skipped"
    else
        fail "Zip file was not skipped"
    fi
fi

echo "======================================"
echo "  Test Summary"
echo "======================================"
echo "Total tests run: $TESTS_RUN"
echo -e "Assertions Passed: $TESTS_PASSED"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Tests Failed: $TESTS_FAILED${NC}"
    cleanup
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    cleanup
    exit 0
fi
