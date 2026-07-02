#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "--- Python lint (ruff) ---"
ruff check .
ruff format --check .

echo "--- C lint (clang-format) ---"
find libbubblehub -type f \( -name '*.c' -o -name '*.h' \) -print0 | xargs -0 -r clang-format --dry-run --Werror
echo "All lint checks passed."
