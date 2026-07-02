#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

meson test -C libbubblehub/build --print-errorlogs
pytest -m "not integration" "$@"
