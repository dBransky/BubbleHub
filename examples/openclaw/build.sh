#!/usr/bin/env bash
set -euo pipefail

NODE_VERSION="${NODE_VERSION:-22.19.0}"
PNPM_VERSION="${PNPM_VERSION:-11.2.2}"

if ! command -v node >/dev/null 2>&1 || ! node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)"; then
  echo "Node.js ${NODE_VERSION} or newer is required. Install Node.js, then rerun this script." >&2
  exit 1
fi

if ! command -v pnpm >/dev/null 2>&1; then
  corepack enable
  corepack prepare "pnpm@${PNPM_VERSION}" --activate
fi

if [[ ! -d openclaw ]]; then
  git clone https://github.com/openclaw/openclaw.git --depth 1
fi
cd openclaw

pnpm install --store-dir .pnpm-store