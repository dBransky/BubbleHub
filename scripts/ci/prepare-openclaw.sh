#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_PARENT="$ROOT/examples/openclaw"
OPENCLAW_CACHE_DIR="${OPENCLAW_CACHE_DIR:-/cache/openclaw}"
OPENCLAW_DIR="$OPENCLAW_CACHE_DIR/openclaw"
OPENCLAW_LINK="$OPENCLAW_PARENT/openclaw"
OPENCLAW_REPO="${OPENCLAW_REPO:-https://github.com/openclaw/openclaw.git}"
OPENCLAW_REF="${OPENCLAW_REF:-153fed790a5c0a8dda7a30a055f1d8203937e0e1}"

mkdir -p "$OPENCLAW_PARENT" "$OPENCLAW_CACHE_DIR"

if [[ ! -d "$OPENCLAW_DIR/.git" ]]; then
  rm -rf "$OPENCLAW_DIR"
  git clone --no-checkout "$OPENCLAW_REPO" "$OPENCLAW_DIR"
fi

git -c "safe.directory=$OPENCLAW_DIR" -C "$OPENCLAW_DIR" fetch --depth 1 origin "$OPENCLAW_REF"
git -c "safe.directory=$OPENCLAW_DIR" -C "$OPENCLAW_DIR" checkout --force FETCH_HEAD

if [[ -e "$OPENCLAW_LINK" && ! -L "$OPENCLAW_LINK" ]]; then
  rm -rf "$OPENCLAW_LINK"
fi
ln -sfn "$OPENCLAW_DIR" "$OPENCLAW_LINK"
