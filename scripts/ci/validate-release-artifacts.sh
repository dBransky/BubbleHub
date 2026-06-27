#!/usr/bin/env bash
set -euo pipefail

ASSETS_DIR="${1:?usage: scripts/ci/validate-release-artifacts.sh <release-assets-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGEOS_RUNTIME_IMAGE="${AGEOS_RUNTIME_IMAGE:?AGEOS_RUNTIME_IMAGE is required}"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required release artifact: $path" >&2
    exit 1
  fi
}

echo "Validating release artifacts in ${ASSETS_DIR}..."

TARBALL="$ASSETS_DIR/ageos-source.tar.gz"
INSTALL_SH="$ASSETS_DIR/install.sh"
INSTALL_PS1="$ASSETS_DIR/install.ps1"
CHECKSUMS="$ASSETS_DIR/SHA256SUMS"
CONTAINER_IMAGE="$ASSETS_DIR/container-image.txt"
shopt -s nullglob
DEBS=("$ASSETS_DIR"/AgeOS-*-x64.deb)
EXES=("$ASSETS_DIR"/AgeOS-*-x64.exe)
shopt -u nullglob

require_file "$TARBALL"
require_file "$INSTALL_SH"
require_file "$INSTALL_PS1"
require_file "$CHECKSUMS"
require_file "$CONTAINER_IMAGE"
if [[ ${#DEBS[@]} -eq 0 || ${#EXES[@]} -eq 0 ]]; then
  echo "Expected AgeOS-*-x64.deb and AgeOS-*-x64.exe in ${ASSETS_DIR}" >&2
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

echo "Pulling runtime image ${AGEOS_RUNTIME_IMAGE}..."
docker pull "$AGEOS_RUNTIME_IMAGE"

echo "Validating mocked GPU setup branches..."
docker run --rm "$AGEOS_RUNTIME_IMAGE" bash -lc '
  set -euo pipefail
  profile="$(mktemp)"
  AGEOS_GPU_VENDOR=nvidia \
  AGEOS_GPU_BACKENDS=cuda-llama \
  AGEOS_GPU_BACKEND=cuda-llama \
  AGEOS_GPU_VRAM_BYTES=11811160064 \
  AGEOS_GPU_FREE_VRAM_BYTES=9663676416 \
    /opt/ageos/bin/python -m ageos.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"cuda-llama\"" "$profile"

  AGEOS_GPU_VENDOR=nvidia \
  AGEOS_GPU_BACKENDS=vllm,cuda-llama \
  AGEOS_GPU_BACKEND=vllm \
  AGEOS_GPU_COMPUTE_CAPABILITY=8.9 \
  AGEOS_GPU_VRAM_BYTES=25769803776 \
  AGEOS_GPU_FREE_VRAM_BYTES=23622320128 \
    /opt/ageos/bin/python -m ageos.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"vllm\"" "$profile"

  AGEOS_GPU_VENDOR=none \
  AGEOS_GPU_BACKENDS= \
  AGEOS_GPU_BACKEND=cpu \
    /opt/ageos/bin/python -m ageos.gpu_setup --mode auto --no-install --profile-out "$profile"
  grep -q "\"backend\": \"cpu\"" "$profile"
'

echo "Checking vLLM optional dependency resolution..."
docker run --rm "$AGEOS_RUNTIME_IMAGE" bash -lc '
  set -euo pipefail
  /opt/ageos/bin/python -m pip install --dry-run "vllm>=0.5" >/tmp/ageos-vllm-dry-run.log
'

echo "Validating source tarball install path (AGEOS_SKIP_DEPS=1 ./scripts/build.sh)..."
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$TARBALL:/tmp/ageos-source.tar.gz:ro" \
  "$AGEOS_RUNTIME_IMAGE" \
  bash -lc '
    set -euo pipefail
    rm -rf /opt/ageos
    rm -f /usr/local/bin/ageos /usr/local/bin/ageos-node
    src="$(mktemp -d)"
    tar -xzf /tmp/ageos-source.tar.gz -C "$src"
    top_level="$(find "$src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    cd "$top_level"
    AGEOS_SKIP_DEPS=1 ./scripts/build.sh
    ageos --help >/dev/null
    command -v ageos-node >/dev/null
    test -x /usr/local/bin/ageos-node
  '

echo "Validating .deb install on clean Ubuntu..."
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$DEB:/tmp/ageos.deb:ro" \
  ubuntu:22.04 \
  bash -lc '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates python3 sudo
    apt-get install -y /tmp/ageos.deb
    command -v ageos >/dev/null
    command -v ageos-node >/dev/null
    command -v ageos-sandbox >/dev/null
    command -v ageos-control-center >/dev/null
    command -v llama-server >/dev/null
    test -d /opt/ageos
    ageos --help >/dev/null
    ageos app --help >/dev/null
    test -x /usr/bin/ageos-node
    test -x /usr/bin/ageos-control-center
    test -f /usr/share/applications/ageos-control-center.desktop
    test -f /usr/share/icons/hicolor/512x512/apps/ageos-control-center.png
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
if ! strings "$EXE" | grep -q 'AgeOS'; then
  echo "Windows bootstrapper does not contain expected AgeOS branding." >&2
  exit 1
fi
if ! strings "$EXE" | grep -q 'Control Center'; then
  echo "Windows bootstrapper does not contain expected Control Center branding." >&2
  exit 1
fi

echo "Release artifact validation passed."
