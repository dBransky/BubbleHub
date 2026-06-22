#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

export AGEOS_RUN_INTEGRATION="${AGEOS_RUN_INTEGRATION:-1}"
export AGEOS_CACHE="${AGEOS_CACHE:-/cache/ageos}"
export AGEOS_MODELS_CONFIG="${AGEOS_MODELS_CONFIG:-$AGEOS_CACHE/ci-models.yaml}"
export AGEOS_INTEGRATION_WORKSPACE_DIR="${AGEOS_INTEGRATION_WORKSPACE_DIR:-$AGEOS_CACHE/integration-workspaces}"
export AGEOS_LLAMA_CTX_SIZE="${AGEOS_LLAMA_CTX_SIZE:-512}"
export AGEOS_MAX_OUTPUT_TOKENS="${AGEOS_MAX_OUTPUT_TOKENS:-32}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-$NO_PROXY}"
export OPENCLAW_CACHE_DIR="${OPENCLAW_CACHE_DIR:-/cache/openclaw}"

mkdir -p "$AGEOS_CACHE" "$AGEOS_INTEGRATION_WORKSPACE_DIR" "$OPENCLAW_CACHE_DIR" /tmp/openclaw

scripts/ci/write-ci-model-config.sh
scripts/ci/prepare-openclaw.sh

pytest -m integration "$@"
