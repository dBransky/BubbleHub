#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COVERAGE_OUT="${COVERAGE_OUT:-/coverage-out}"
C_OUT="${COVERAGE_OUT}/c"

rm -rf "$C_OUT"
mkdir -p "$C_OUT/html"

integration_status=0
c_coverage_status=0
c_html_status=0

scripts/ci/run-integration-tests.sh "$@" || integration_status=$?

gcovr \
  --root "$ROOT/libbubble" \
  --object-directory "$ROOT/libbubble/build" \
  --filter "$ROOT/libbubble/.*\\.c$" \
  --exclude "$ROOT/libbubble/tests/.*" \
  --xml-pretty \
  -o "$C_OUT/coverage.xml" || c_coverage_status=$?

gcovr \
  --root "$ROOT/libbubble" \
  --object-directory "$ROOT/libbubble/build" \
  --filter "$ROOT/libbubble/.*\\.c$" \
  --exclude "$ROOT/libbubble/tests/.*" \
  --html-details "$C_OUT/coverage.html" || c_html_status=$?

chmod -R a+rX "$COVERAGE_OUT"

if (( integration_status != 0 || c_coverage_status != 0 || c_html_status != 0 )); then
  exit 1
fi
