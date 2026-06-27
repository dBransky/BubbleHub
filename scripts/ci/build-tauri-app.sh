#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="$ROOT/app/Cargo.toml"

export CARGO_HTTP_MULTIPLEXING="${CARGO_HTTP_MULTIPLEXING:-false}"

build_tauri_app() {
  cargo fetch --manifest-path "$MANIFEST"
  cargo build --release --manifest-path "$MANIFEST"
  if [[ "${TAURI_RUN_TESTS:-0}" == "1" ]]; then
    cargo test --manifest-path "$MANIFEST"
  fi
}

for attempt in 1 2 3; do
  if build_tauri_app; then
    exit 0
  fi
  if (( attempt == 3 )); then
    break
  fi
  echo "cargo build failed (attempt ${attempt}/3), retrying..." >&2
  sleep $((attempt * 5))
done

exit 1
