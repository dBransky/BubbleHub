#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COVERAGE_OUT="${COVERAGE_OUT:-/coverage-out}"
C_OUT="${COVERAGE_OUT}/c"
PY_OUT="${COVERAGE_OUT}/python"

rm -rf "$C_OUT" "$PY_OUT"
mkdir -p "$C_OUT/html" "$PY_OUT/html"

meson_status=0
pytest_status=0
c_coverage_status=0
c_html_status=0

meson test -C libbubblehub/build --print-errorlogs || meson_status=$?

COVERAGE_FILE="${PY_OUT}/.coverage" pytest -m "not integration" \
    --cov=bubblehub \
    --cov-report=term-missing \
    --cov-report="xml:${PY_OUT}/coverage.xml" \
    --cov-report="html:${PY_OUT}/html" \
    "$@" || pytest_status=$?

ninja -C libbubblehub/build coverage-xml || c_coverage_status=$?
ninja -C libbubblehub/build coverage-html || c_html_status=$?

if [[ -f libbubblehub/build/meson-logs/coverage.xml ]]; then
    cp libbubblehub/build/meson-logs/coverage.xml "$C_OUT/coverage.xml"
fi
if [[ -d libbubblehub/build/meson-logs/coveragereport ]]; then
    rm -rf "$C_OUT/html"
    cp -a libbubblehub/build/meson-logs/coveragereport "$C_OUT/html"
fi

chmod -R a+rX "$COVERAGE_OUT"

if (( meson_status != 0 || pytest_status != 0 || c_coverage_status != 0 || c_html_status != 0 )); then
    exit 1
fi
