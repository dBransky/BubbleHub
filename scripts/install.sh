#!/usr/bin/env bash
set -euo pipefail

REPO="${BUBBLEHUB_REPO:-bublhub/bubblehub}"
VERSION="${BUBBLEHUB_VERSION:-latest}"
ASSET_NAME="${BUBBLEHUB_ASSET_NAME:-bubblehub-source.tar.gz}"
RELEASE_BASE_URL="${BUBBLEHUB_RELEASE_BASE_URL:-}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "BubbleHub installs on Linux. On Windows, run the PowerShell installer with WSL enabled." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to install BubbleHub." >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "tar is required to install BubbleHub." >&2
  exit 1
fi

if [[ -n "$RELEASE_BASE_URL" ]]; then
  RELEASE_URL="${RELEASE_BASE_URL%/}/${VERSION}/${ASSET_NAME}"
elif [[ "$VERSION" == "latest" ]]; then
  RELEASE_URL="https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}"
else
  RELEASE_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET_NAME}"
fi

ARCHIVE="$TMP_DIR/$ASSET_NAME"
SRC_DIR="$TMP_DIR/src"

echo "Downloading BubbleHub ${VERSION} from ${REPO}..."
curl -fsSL "$RELEASE_URL" -o "$ARCHIVE"
mkdir -p "$SRC_DIR"
tar -xzf "$ARCHIVE" -C "$SRC_DIR" --strip-components=1

cd "$SRC_DIR"
. ./scripts/install-ui.sh
BUBBLEHUB_INSTALL_APP="$(bubblehub_resolve_desktop_app_choice)"
export BUBBLEHUB_INSTALL_APP
if [[ "$BUBBLEHUB_INSTALL_APP" == "1" ]]; then
  export BUBBLEHUB_SKIP_TAURI=0
  echo "Desktop app selected: BubbleHub will be installed."
else
  export BUBBLEHUB_SKIP_TAURI=1
  echo "CLI-only install selected. You can install the desktop app later with: bubblehub"
fi
if [[ "${BUBBLEHUB_SKIP_DEPS:-0}" != "1" ]]; then
  ./scripts/install-deps.sh
fi
./scripts/build.sh

echo
echo "BubbleHub installed. Try: bubble --help"
echo "Open the app with: bubblehub"
