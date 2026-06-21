#!/usr/bin/env bash
set -euo pipefail

ROOTFS_DIR="${AGEOS_ROOTFS_DIR:-/opt/ageos/rootfs/ubuntu-26.04}"
ROOTFS_SUITE="${AGEOS_ROOTFS_SUITE:-resolute}"
ROOTFS_VERSION="${AGEOS_ROOTFS_VERSION:-26.04}"
ROOTFS_MIRROR="${AGEOS_ROOTFS_MIRROR:-http://archive.ubuntu.com/ubuntu}"
ROOTFS_COMPONENTS="${AGEOS_ROOTFS_COMPONENTS:-main,restricted,universe,multiverse}"
ROOTFS_ARCH="${AGEOS_ROOTFS_ARCH:-$(dpkg --print-architecture 2>/dev/null || uname -m)}"
SUDO="${SUDO:-sudo}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AgeOS rootfs creation is Linux-only." >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=""
fi

if [[ "${AGEOS_SKIP_ROOTFS:-0}" == "1" ]]; then
  echo "Skipping AgeOS Ubuntu rootfs creation because AGEOS_SKIP_ROOTFS=1."
  exit 0
fi

if ! command -v debootstrap >/dev/null 2>&1; then
  echo "debootstrap is required to create the AgeOS Ubuntu rootfs. Run ./scripts/install-deps.sh first." >&2
  exit 1
fi

STAMP="$ROOTFS_DIR/.ageos-rootfs.json"
if [[ -f "$STAMP" ]] && grep -q "\"suite\": \"${ROOTFS_SUITE}\"" "$STAMP" && grep -q "\"version\": \"${ROOTFS_VERSION}\"" "$STAMP"; then
  echo "AgeOS Ubuntu rootfs already exists at ${ROOTFS_DIR}."
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${TMP_DIR:-}" ]]; then
    ${SUDO} rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

PACKAGES="${AGEOS_ROOTFS_PACKAGES:-bash,ca-certificates,coreutils,curl,git,locales,python3,python3-venv,sudo,tzdata,xz-utils}"
APT_COMPONENTS="${ROOTFS_COMPONENTS//,/ }"

echo "Creating AgeOS Ubuntu ${ROOTFS_VERSION} rootfs (${ROOTFS_SUITE}) at ${ROOTFS_DIR}..."
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

${SUDO} mkdir -p "$TMP_DIR/rootfs/opt/ageos" "$TMP_DIR/rootfs/workspace"
${SUDO} tee "$TMP_DIR/rootfs/.ageos-rootfs.json" >/dev/null <<EOF
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
echo "AgeOS Ubuntu rootfs is ready at ${ROOTFS_DIR}."
