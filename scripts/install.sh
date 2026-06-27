#!/usr/bin/env bash
set -euo pipefail

REPO="${AGEOS_REPO:-ageos-labs/ageos-runtime}"
VERSION="${AGEOS_VERSION:-latest}"
ASSET_NAME="${AGEOS_ASSET_NAME:-ageos-source.tar.gz}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AgeOS installs on Linux. On Windows, run the PowerShell installer with WSL enabled." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to install AgeOS." >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "tar is required to install AgeOS." >&2
  exit 1
fi

if [[ "$VERSION" == "latest" ]]; then
  RELEASE_URL="https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}"
else
  RELEASE_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET_NAME}"
fi

ARCHIVE="$TMP_DIR/$ASSET_NAME"
SRC_DIR="$TMP_DIR/src"

echo "Downloading AgeOS ${VERSION} from ${REPO}..."
curl -fsSL "$RELEASE_URL" -o "$ARCHIVE"
mkdir -p "$SRC_DIR"
tar -xzf "$ARCHIVE" -C "$SRC_DIR" --strip-components=1

cd "$SRC_DIR"
. ./scripts/install-ui.sh
AGEOS_INSTALL_APP="$(ageos_resolve_desktop_app_choice)"
export AGEOS_INSTALL_APP
if [[ "$AGEOS_INSTALL_APP" == "1" ]]; then
  export AGEOS_SKIP_TAURI=0
  echo "Desktop app selected: AgeOS Control Center will be installed."
else
  export AGEOS_SKIP_TAURI=1
  echo "CLI-only install selected. You can install the desktop app later with: ageos app"
fi
if [[ "${AGEOS_SKIP_DEPS:-0}" != "1" ]]; then
  ./scripts/install-deps.sh
fi
./scripts/build.sh

echo
echo "AgeOS installed. Try: ageos --help"
