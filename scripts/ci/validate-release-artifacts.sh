#!/usr/bin/env bash
set -euo pipefail

ASSETS_DIR="${1:?usage: scripts/ci/validate-release-artifacts.sh <release-assets-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUBBLEHUB_RUNTIME_IMAGE="${BUBBLEHUB_RUNTIME_IMAGE:?BUBBLEHUB_RUNTIME_IMAGE is required}"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required release artifact: $path" >&2
    exit 1
  fi
}

echo "Validating release artifacts in ${ASSETS_DIR}..."

TARBALL="$ASSETS_DIR/bubblehub-source.tar.gz"
INSTALL_SH="$ASSETS_DIR/install.sh"
INSTALL_PS1="$ASSETS_DIR/install.ps1"
CHECKSUMS="$ASSETS_DIR/SHA256SUMS"
CONTAINER_IMAGE="$ASSETS_DIR/container-image.txt"
shopt -s nullglob
DEBS=("$ASSETS_DIR"/BubbleHub-*-x64.deb)
EXES=("$ASSETS_DIR"/BubbleHub-*-x64.exe)
shopt -u nullglob

require_file "$TARBALL"
require_file "$INSTALL_SH"
require_file "$INSTALL_PS1"
require_file "$CHECKSUMS"
require_file "$CONTAINER_IMAGE"
if [[ ${#DEBS[@]} -eq 0 || ${#EXES[@]} -eq 0 ]]; then
  echo "Expected BubbleHub-*-x64.deb and BubbleHub-*-x64.exe in ${ASSETS_DIR}" >&2
  exit 1
fi

DEB="${DEBS[0]}"
EXE="${EXES[0]}"

echo "Checking repository scripts..."
while IFS= read -r script; do
  bash -n "$script"
done < <(find "$ROOT/scripts" -type f -name '*.sh' | sort)

echo "Checking release installer scripts..."
bash -n "$INSTALL_SH"
bash -n "$ROOT/scripts/install.sh"
bash -n "$ROOT/scripts/install-deps.sh"
bash -n "$ROOT/scripts/build.sh"
bash -n "$ROOT/scripts/package-release.sh"

if command -v pwsh >/dev/null 2>&1; then
  validate_ps1() {
    local script_path="$1"
    PS1_PATH="$script_path" pwsh -NoProfile -Command '
      $errors = $null
      $tokens = $null
      $null = [System.Management.Automation.Language.Parser]::ParseFile($env:PS1_PATH, [ref]$tokens, [ref]$errors)
      if ($errors) {
        $errors | ForEach-Object { Write-Error $_.ToString() }
        exit 1
      }
    '
  }
  validate_ps1 "$INSTALL_PS1"
  validate_ps1 "$ROOT/scripts/install.ps1"
else
  echo "pwsh not available; skipping PowerShell syntax checks." >&2
fi

echo "Checking SHA256SUMS..."
(
  cd "$ASSETS_DIR"
  sha256sum -c SHA256SUMS
)

echo "Checking source tarball layout..."
EXTRACT_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$EXTRACT_DIR"
}
trap cleanup EXIT
tar -xzf "$TARBALL" -C "$EXTRACT_DIR"
TOP_LEVEL="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
require_file "$TOP_LEVEL/pyproject.toml"
require_file "$TOP_LEVEL/scripts/build.sh"
require_file "$TOP_LEVEL/scripts/install.sh"
require_file "$TOP_LEVEL/scripts/install-deps.sh"
while IFS= read -r script; do
  bash -n "$script"
done < <(find "$TOP_LEVEL/scripts" -type f -name '*.sh' | sort)

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to validate install and package artifacts." >&2
  exit 1
fi

echo "Pulling runtime image ${BUBBLEHUB_RUNTIME_IMAGE}..."
docker pull "$BUBBLEHUB_RUNTIME_IMAGE"

echo "Validating mocked GPU setup branches..."
docker run --rm "$BUBBLEHUB_RUNTIME_IMAGE" bash -lc '
  set -euo pipefail
  profile="$(mktemp)"
  BUBBLEHUB_GPU_VENDOR=nvidia \
  BUBBLEHUB_GPU_BACKENDS=cuda-llama \
  BUBBLEHUB_GPU_BACKEND=cuda-llama \
  BUBBLEHUB_GPU_VRAM_BYTES=11811160064 \
  BUBBLEHUB_GPU_FREE_VRAM_BYTES=9663676416 \
    /opt/bubblehub/bin/python -m bubblehub.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"cuda-llama\"" "$profile"

  BUBBLEHUB_GPU_VENDOR=nvidia \
  BUBBLEHUB_GPU_BACKENDS=vllm,cuda-llama \
  BUBBLEHUB_GPU_BACKEND=vllm \
  BUBBLEHUB_GPU_COMPUTE_CAPABILITY=8.9 \
  BUBBLEHUB_GPU_VRAM_BYTES=25769803776 \
  BUBBLEHUB_GPU_FREE_VRAM_BYTES=23622320128 \
    /opt/bubblehub/bin/python -m bubblehub.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"vllm\"" "$profile"

  BUBBLEHUB_GPU_VENDOR=none \
  BUBBLEHUB_GPU_BACKENDS= \
  BUBBLEHUB_GPU_BACKEND=cpu \
    /opt/bubblehub/bin/python -m bubblehub.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"cpu\"" "$profile"
'

echo "Checking vLLM optional dependency resolution..."
docker run --rm "$BUBBLEHUB_RUNTIME_IMAGE" bash -lc '
  set -euo pipefail
  /opt/bubblehub/bin/python -m pip install --dry-run "vllm>=0.5" >/tmp/bubblehub-vllm-dry-run.log
'

echo "Validating source tarball install path (BUBBLEHUB_SKIP_DEPS=1 ./scripts/build.sh)..."
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$TARBALL:/tmp/bubblehub-source.tar.gz:ro" \
  "$BUBBLEHUB_RUNTIME_IMAGE" \
  bash -lc '
    set -euo pipefail
    rm -rf /opt/bubblehub
    rm -f /usr/local/bin/bubble /usr/local/bin/bubblehub /usr/local/bin/bubblehub-node
    src="$(mktemp -d)"
    tar -xzf /tmp/bubblehub-source.tar.gz -C "$src"
    top_level="$(find "$src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    cd "$top_level"
    BUBBLEHUB_SKIP_DEPS=1 ./scripts/build.sh
    bubble --help >/dev/null
    bubblehub --help >/dev/null
    command -v bubblehub-node >/dev/null
    test -x /usr/local/bin/bubblehub-node
  '

echo "Validating .deb install on clean Ubuntu..."
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$DEB:/tmp/bubblehub.deb:ro" \
  ubuntu:22.04 \
  bash -lc '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates python3 sudo
    apt-get install -y /tmp/bubblehub.deb
    command -v bubble >/dev/null
    command -v bubblehub >/dev/null
    command -v bubblehub-node >/dev/null
    command -v bubblehub-sandbox >/dev/null
    command -v llama-server >/dev/null
    test -d /opt/bubblehub
    bubble --help >/dev/null
    bubblehub --help >/dev/null
    test -x /usr/bin/bubblehub-node
    test -x /opt/bubblehub/share/bubblehub/app/bubblehub
    test -f /usr/share/applications/bubblehub.desktop
    test -f /usr/share/icons/hicolor/scalable/apps/bubblehub.svg
  '

echo "Validating Windows bootstrapper .exe..."
if [[ ! -s "$EXE" ]]; then
  echo "Windows bootstrapper is empty: $EXE" >&2
  exit 1
fi
if ! file "$EXE" | grep -Eiq 'PE32|PE32\+'; then
  echo "Windows bootstrapper is not a PE executable: $EXE" >&2
  file "$EXE" >&2 || true
  exit 1
fi
if ! strings "$EXE" | grep -q 'BubbleHub'; then
  echo "Windows bootstrapper does not contain expected BubbleHub branding." >&2
  exit 1
fi
if ! strings "$EXE" | grep -q 'desktop app'; then
  echo "Windows bootstrapper does not contain expected desktop app branding." >&2
  exit 1
fi

echo "Release artifact validation passed."
