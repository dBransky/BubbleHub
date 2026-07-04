#!/usr/bin/env bash
set -euo pipefail

ROOTFS_DIR="${BUBBLEHUB_ROOTFS_DIR:-/opt/bubblehub/rootfs/ubuntu-26.04}"
ROOTFS_SUITE="${BUBBLEHUB_ROOTFS_SUITE:-resolute}"
ROOTFS_VERSION="${BUBBLEHUB_ROOTFS_VERSION:-26.04}"
ROOTFS_MIRROR="${BUBBLEHUB_ROOTFS_MIRROR:-http://archive.ubuntu.com/ubuntu}"
ROOTFS_COMPONENTS="${BUBBLEHUB_ROOTFS_COMPONENTS:-main,restricted,universe,multiverse}"
ROOTFS_ARCH="${BUBBLEHUB_ROOTFS_ARCH:-$(dpkg --print-architecture 2>/dev/null || uname -m)}"
SUDO="${SUDO:-sudo}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "BubbleHub rootfs creation is Linux-only." >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=""
fi

if [[ "${BUBBLEHUB_SKIP_ROOTFS:-0}" == "1" ]]; then
  echo "Skipping BubbleHub Ubuntu rootfs creation because BUBBLEHUB_SKIP_ROOTFS=1."
  exit 0
fi

if ! command -v debootstrap >/dev/null 2>&1; then
  echo "debootstrap is required to create the BubbleHub Ubuntu rootfs. Run ./scripts/install-deps.sh first." >&2
  exit 1
fi

STAMP="$ROOTFS_DIR/.bubblehub-rootfs.json"
if [[ -f "$STAMP" ]] && grep -q "\"suite\": \"${ROOTFS_SUITE}\"" "$STAMP" && grep -q "\"version\": \"${ROOTFS_VERSION}\"" "$STAMP"; then
  echo "BubbleHub Ubuntu rootfs already exists at ${ROOTFS_DIR}."
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${TMP_DIR:-}" ]]; then
    ${SUDO} rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

PACKAGES="${BUBBLEHUB_ROOTFS_PACKAGES:-bash,ca-certificates,coreutils,curl,git,locales,python3,python3-venv,sudo,tzdata,xz-utils}"
APT_COMPONENTS="${ROOTFS_COMPONENTS//,/ }"

echo "Creating BubbleHub Ubuntu ${ROOTFS_VERSION} rootfs (${ROOTFS_SUITE}) at ${ROOTFS_DIR}..."
${SUDO} rm -rf "$TMP_DIR/rootfs" "$ROOTFS_DIR"
${SUDO} mkdir -p "$TMP_DIR/rootfs" "$(dirname "$ROOTFS_DIR")"
${SUDO} debootstrap \
  --arch="$ROOTFS_ARCH" \
  --variant=minbase \
  --components="$ROOTFS_COMPONENTS" \
  --include="$PACKAGES" \
  "$ROOTFS_SUITE" \
  "$TMP_DIR/rootfs" \
  "$ROOTFS_MIRROR"

${SUDO} tee "$TMP_DIR/rootfs/etc/apt/sources.list" >/dev/null <<EOF
deb ${ROOTFS_MIRROR} ${ROOTFS_SUITE} ${APT_COMPONENTS}
deb ${ROOTFS_MIRROR} ${ROOTFS_SUITE}-updates ${APT_COMPONENTS}
deb ${ROOTFS_MIRROR} ${ROOTFS_SUITE}-security ${APT_COMPONENTS}
EOF

${SUDO} mkdir -p "$TMP_DIR/rootfs/opt/bubblehub" "$TMP_DIR/rootfs/workspace"
${SUDO} touch \
  "$TMP_DIR/rootfs/usr/bin/bubble" \
  "$TMP_DIR/rootfs/usr/bin/bubblehub" \
  "$TMP_DIR/rootfs/usr/bin/bubblehub-node" \
  "$TMP_DIR/rootfs/usr/bin/bubblehub-sandbox" \
  "$TMP_DIR/rootfs/usr/bin/llama-server" \
  "$TMP_DIR/rootfs/usr/lib/libbubble.so" \
  "$TMP_DIR/rootfs/usr/lib/libbubblehub.so"
${SUDO} mkdir -p "$TMP_DIR/rootfs/usr/lib/x86_64-linux-gnu"
${SUDO} touch \
  "$TMP_DIR/rootfs/usr/lib/x86_64-linux-gnu/libbubble.so" \
  "$TMP_DIR/rootfs/usr/lib/x86_64-linux-gnu/libbubblehub.so"
${SUDO} tee "$TMP_DIR/rootfs/.bubblehub-rootfs.json" >/dev/null <<EOF
{
  "name": "ubuntu",
  "version": "${ROOTFS_VERSION}",
  "suite": "${ROOTFS_SUITE}",
  "arch": "${ROOTFS_ARCH}",
  "components": "${ROOTFS_COMPONENTS}",
  "packages": "${PACKAGES}",
  "mirror": "${ROOTFS_MIRROR}"
}
EOF

${SUDO} mv "$TMP_DIR/rootfs" "$ROOTFS_DIR"
echo "BubbleHub Ubuntu rootfs is ready at ${ROOTFS_DIR}."
