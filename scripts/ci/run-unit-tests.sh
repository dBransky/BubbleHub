#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

meson test -C libageos/build --print-errorlogs
pytest -m "not integration" "$@"
