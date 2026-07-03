#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "BubbleHub desktop app dependencies are installed inside Linux or WSL."
  exit 0
fi

export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export TZ="${TZ:-Etc/UTC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo -E apt-get update
sudo -E apt-get install -y \
  libatk1.0-dev \
  libayatana-appindicator3-dev \
  libcairo2-dev \
  libgdk-pixbuf-2.0-dev \
  libgtk-3-dev \
  libpango1.0-dev \
  librsvg2-dev \
  libssl-dev \
  libxdo-dev

WEBKIT_GTK_PACKAGE="libwebkit2gtk-4.1-dev"
if ! apt-cache show "$WEBKIT_GTK_PACKAGE" >/dev/null 2>&1; then
  WEBKIT_GTK_PACKAGE="libwebkit2gtk-4.0-dev"
fi
sudo -E apt-get install -y "$WEBKIT_GTK_PACKAGE"

bash "$SCRIPT_DIR/install-rust.sh"
