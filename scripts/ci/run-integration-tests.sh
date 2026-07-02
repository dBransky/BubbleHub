#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

export BUBBLEHUB_RUN_INTEGRATION="${BUBBLEHUB_RUN_INTEGRATION:-1}"
export BUBBLEHUB_CACHE="${BUBBLEHUB_CACHE:-/cache/bubblehub}"
export BUBBLEHUB_MODELS_CONFIG="${BUBBLEHUB_MODELS_CONFIG:-$BUBBLEHUB_CACHE/ci-models.yaml}"
export BUBBLEHUB_INTEGRATION_WORKSPACE_DIR="${BUBBLEHUB_INTEGRATION_WORKSPACE_DIR:-$BUBBLEHUB_CACHE/integration-workspaces}"
export BUBBLEHUB_LLAMA_CTX_SIZE="${BUBBLEHUB_LLAMA_CTX_SIZE:-512}"
export BUBBLEHUB_MAX_OUTPUT_TOKENS="${BUBBLEHUB_MAX_OUTPUT_TOKENS:-32}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-$NO_PROXY}"
export OPENCLAW_CACHE_DIR="${OPENCLAW_CACHE_DIR:-/cache/openclaw}"

mkdir -p "$BUBBLEHUB_CACHE" "$BUBBLEHUB_INTEGRATION_WORKSPACE_DIR" "$OPENCLAW_CACHE_DIR" /tmp/openclaw

scripts/ci/write-ci-model-config.sh
scripts/ci/prepare-openclaw.sh

pytest -m integration "$@"
