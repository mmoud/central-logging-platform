#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
temporary_dir=$(mktemp -d)
trap 'rm -rf "$temporary_dir"' EXIT
environment_file="$temporary_dir/test.env"
cat >"$environment_file" <<EOF
DATA_DIR=$temporary_dir
STORAGE_WARNING_PERCENT=75
STORAGE_CRITICAL_PERCENT=85
STORAGE_CHECK_TEST_MODE=true
EOF

run_check() {
  local percentage=$1 expected_code=$2 expected_state=$3 output code
  set +e
  output=$(STORAGE_CHECK_ENV_FILE="$environment_file" \
    STORAGE_CHECK_PERCENT_OVERRIDE="$percentage" \
    "$ROOT_DIR/scripts/storage-check.sh" --check 2>&1)
  code=$?
  set -e
  [ "$code" -eq "$expected_code" ] || {
    printf 'Expected exit %s for %s%%, got %s: %s\n' \
      "$expected_code" "$percentage" "$code" "$output" >&2
    exit 1
  }
  grep -q "Storage $expected_state:" <<<"$output"
}

run_check 50 0 OK
run_check 75 1 WARNING
run_check 85 2 CRITICAL

STORAGE_CHECK_ENV_FILE="$environment_file" STORAGE_CHECK_PERCENT_OVERRIDE=95 \
  "$ROOT_DIR/scripts/storage-check.sh" --status | grep -q 'Storage CRITICAL:'

# Exercise the real df parser as well as deterministic threshold branches.
cat >"$environment_file" <<EOF
DATA_DIR=$temporary_dir
STORAGE_WARNING_PERCENT=98
STORAGE_CRITICAL_PERCENT=99
STORAGE_CHECK_TEST_MODE=false
EOF
STORAGE_CHECK_ENV_FILE="$environment_file" \
  "$ROOT_DIR/scripts/storage-check.sh" --check | grep -q 'Storage OK:'
echo 'Storage threshold checks passed.'
