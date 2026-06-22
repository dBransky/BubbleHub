#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_PREFIX="${AGEOS_PREFIX:-/opt/ageos}"
BIN_DIR="${AGEOS_BIN_DIR:-/usr/local/bin}"
BUILD_DIR="$ROOT/libageos/build"
C_SOURCE_DIR="$ROOT/libageos"
SUDO="${SUDO:-sudo}"
AGEOS_GPU_MODE="${AGEOS_GPU:-auto}"
ROOTFS_DIR="${AGEOS_ROOTFS_DIR:-$INSTALL_PREFIX/rootfs/ubuntu-26.04}"
ROOTFS_SUITE="${AGEOS_ROOTFS_SUITE:-resolute}"
ROOTFS_VERSION="${AGEOS_ROOTFS_VERSION:-26.04}"
NATIVE_STAGE=""
PY_WHEEL_DIR=""
PY_BUILD_ENV=""
PRESERVED_ROOTFS=""
PRESERVED_ROOTFS_PARENT=""

cleanup() {
  if [[ -n "$NATIVE_STAGE" ]]; then
    rm -rf "$NATIVE_STAGE"
  fi
  if [[ -n "$PY_WHEEL_DIR" ]]; then
    rm -rf "$PY_WHEEL_DIR"
  fi
  if [[ -n "$PY_BUILD_ENV" ]]; then
    rm -rf "$PY_BUILD_ENV"
  fi
  if [[ -n "$PRESERVED_ROOTFS" && -e "$PRESERVED_ROOTFS" ]]; then
    ${SUDO} mkdir -p "$(dirname "$ROOTFS_DIR")"
    ${SUDO} mv "$PRESERVED_ROOTFS" "$ROOTFS_DIR"
    PRESERVED_ROOTFS=""
  fi
  if [[ -n "$PRESERVED_ROOTFS_PARENT" ]]; then
    ${SUDO} rm -rf "$PRESERVED_ROOTFS_PARENT"
  fi
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "AgeOS system-wide source install is Linux-only." >&2
  echo "Use the packaged CLI on Linux or set up a development venv manually on this platform." >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=""
fi

if ! command -v meson >/dev/null 2>&1; then
  echo "meson not found. Run ./scripts/install-deps.sh first." >&2
  exit 1
fi

rootfs_is_current() {
  local stamp="$ROOTFS_DIR/.ageos-rootfs.json"
  [[ -f "$stamp" ]] &&
    grep -q "\"suite\": \"${ROOTFS_SUITE}\"" "$stamp" &&
    grep -q "\"version\": \"${ROOTFS_VERSION}\"" "$stamp"
}

echo "Building native AgeOS core..."
if [[ -f "$BUILD_DIR/meson-private/coredata.dat" ]]; then
  meson setup "$BUILD_DIR" "$C_SOURCE_DIR" --wipe --prefix=/usr/local
else
  rm -rf "$BUILD_DIR"
  meson setup "$BUILD_DIR" "$C_SOURCE_DIR" --prefix=/usr/local
fi
meson compile -C "$BUILD_DIR"
NATIVE_STAGE="$(mktemp -d)"
meson install -C "$BUILD_DIR" --no-rebuild --destdir "$NATIVE_STAGE"
${SUDO} mkdir -p /usr/local
${SUDO} cp -a --remove-destination "$NATIVE_STAGE/usr/local/." /usr/local/
if command -v ldconfig >/dev/null 2>&1; then
  ${SUDO} ldconfig
fi

echo "Building AgeOS Python wheel..."
PY_WHEEL_DIR="$(mktemp -d)"
PY_BUILD_ENV="$(mktemp -d)"
"$PYTHON_BIN" -m venv "$PY_BUILD_ENV"
"$PY_BUILD_ENV/bin/python" -m pip install --upgrade pip build
"$PY_BUILD_ENV/bin/python" -m build --wheel --outdir "$PY_WHEEL_DIR" "$ROOT"
shopt -s nullglob
AGEOS_WHEELS=("$PY_WHEEL_DIR"/ageos-*.whl)
shopt -u nullglob
if [[ ${#AGEOS_WHEELS[@]} -eq 0 ]]; then
  echo "Failed to build AgeOS wheel." >&2
  exit 1
fi

echo "Installing AgeOS Python runtime into ${INSTALL_PREFIX}..."
if [[ "${AGEOS_SKIP_ROOTFS:-0}" != "1" ]] && rootfs_is_current; then
  PRESERVED_ROOTFS_PARENT="$(mktemp -d)"
  PRESERVED_ROOTFS="$PRESERVED_ROOTFS_PARENT/ubuntu-26.04"
  echo "Preserving existing AgeOS Ubuntu rootfs for fast rebuild: ${ROOTFS_DIR}"
  ${SUDO} mv "$ROOTFS_DIR" "$PRESERVED_ROOTFS"
fi
${SUDO} rm -rf "$INSTALL_PREFIX"
${SUDO} mkdir -p "$INSTALL_PREFIX"
if [[ -n "$PRESERVED_ROOTFS" ]]; then
  ${SUDO} mkdir -p "$(dirname "$ROOTFS_DIR")"
  ${SUDO} mv "$PRESERVED_ROOTFS" "$ROOTFS_DIR"
  PRESERVED_ROOTFS=""
fi
${SUDO} "$PYTHON_BIN" -m venv "$INSTALL_PREFIX"
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install --upgrade pip
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install --find-links "$PY_WHEEL_DIR" "${AGEOS_WHEELS[0]}"
${SUDO} env AGEOS_GPU="$AGEOS_GPU_MODE" "$INSTALL_PREFIX/bin/python" -m ageos.gpu_setup \
  --mode "$AGEOS_GPU_MODE" \
  --wheel "${AGEOS_WHEELS[0]}" \
  --profile-out "$INSTALL_PREFIX/install-profile.json"
${SUDO} mv "$INSTALL_PREFIX/bin/ageos" "$INSTALL_PREFIX/bin/ageos-entrypoint"
${SUDO} mv "$INSTALL_PREFIX/bin/ageos-node" "$INSTALL_PREFIX/bin/ageos-node-entrypoint"

echo "Linking global AgeOS commands into ${BIN_DIR}..."
${SUDO} mkdir -p "$BIN_DIR"
${SUDO} rm -f "$BIN_DIR/ageos" "$BIN_DIR/ageos-node"
${SUDO} tee "$BIN_DIR/ageos" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import sys; sys.argv[0] = "ageos"; from ageos.cli.main import run_cli; run_cli()' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/ageos"
${SUDO} tee "$BIN_DIR/ageos-node" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import sys; sys.argv[0] = "ageos-node"; from ageos.node.daemon import main; raise SystemExit(main())' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/ageos-node"
${SUDO} ln -sf "$INSTALL_PREFIX/bin/pytest" "$BIN_DIR/pytest"
if [[ -x /usr/local/bin/ageos-sandbox && "$BIN_DIR/ageos-sandbox" != "/usr/local/bin/ageos-sandbox" ]]; then
  ${SUDO} ln -sf /usr/local/bin/ageos-sandbox "$BIN_DIR/ageos-sandbox"
fi

if [[ "${AGEOS_SKIP_ROOTFS:-0}" == "1" ]]; then
  echo "Skipping AgeOS Ubuntu rootfs because AGEOS_SKIP_ROOTFS=1."
elif rootfs_is_current; then
  echo "AgeOS Ubuntu rootfs already exists at ${ROOTFS_DIR}; skipping rootfs creation."
else
  echo "Creating AgeOS Ubuntu rootfs..."
  AGEOS_ROOTFS_DIR="$ROOTFS_DIR" SUDO="$SUDO" "$ROOT/scripts/create-rootfs.sh"
fi

echo
echo "AgeOS system install is ready."
echo "Run: ageos --help"
