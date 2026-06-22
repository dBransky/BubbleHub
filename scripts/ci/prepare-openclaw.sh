#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_PARENT="$ROOT/examples/openclaw"
OPENCLAW_CACHE_DIR="${OPENCLAW_CACHE_DIR:-/cache/openclaw}"
OPENCLAW_DIR="$OPENCLAW_CACHE_DIR/openclaw"
OPENCLAW_LINK="$OPENCLAW_PARENT/openclaw"

mkdir -p "$OPENCLAW_PARENT" "$OPENCLAW_CACHE_DIR"

if [[ ! -d "$OPENCLAW_DIR/.git" ]]; then
  rm -rf "$OPENCLAW_DIR"
  git clone https://github.com/openclaw/openclaw.git --depth 1 "$OPENCLAW_DIR"
fi

if [[ -e "$OPENCLAW_LINK" && ! -L "$OPENCLAW_LINK" ]]; then
  rm -rf "$OPENCLAW_LINK"
fi
ln -sfn "$OPENCLAW_DIR" "$OPENCLAW_LINK"
