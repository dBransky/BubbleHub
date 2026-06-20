#!/usr/bin/env bash
set -euo pipefail

export PNPM_HOME="${PNPM_HOME:-$HOME/.local/share/pnpm}"
export PATH="$PNPM_HOME/bin:$PNPM_HOME:$PATH"
NODE_VERSION="${NODE_VERSION:-22.19.0}"

if ! command -v pnpm >/dev/null 2>&1; then
  curl -fsSL https://get.pnpm.io/install.sh | sh -
  export PATH="$PNPM_HOME/bin:$PNPM_HOME:$PATH"
fi

if ! command -v node >/dev/null 2>&1 || ! node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)"; then
  pnpm runtime set node "$NODE_VERSION" -g
  export PATH="$PNPM_HOME/bin:$PNPM_HOME:$PATH"
fi

if [[ ! -d openclaw ]]; then
  git clone https://github.com/openclaw/openclaw.git --depth 1
fi
cd openclaw

pnpm install