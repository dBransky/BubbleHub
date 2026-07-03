#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

. "$ROOT/scripts/install-ui.sh"
BUBBLEHUB_INSTALL_APP="$(bubblehub_resolve_desktop_app_choice)"
export BUBBLEHUB_INSTALL_APP
if [[ "${BUBBLEHUB_SKIP_TAURI:-0}" == "1" ]]; then
  export BUBBLEHUB_SKIP_TAURI=1
elif [[ "$BUBBLEHUB_INSTALL_APP" == "1" ]]; then
  export BUBBLEHUB_SKIP_TAURI=0
else
  export BUBBLEHUB_SKIP_TAURI=1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_PREFIX="${BUBBLEHUB_PREFIX:-/opt/bubblehub}"
BIN_DIR="${BUBBLEHUB_BIN_DIR:-/usr/local/bin}"
BUILD_DIR="$ROOT/libbubble/build"
C_SOURCE_DIR="$ROOT/libbubble"
SUDO="${SUDO:-sudo}"
BUBBLEHUB_GPU_MODE="${BUBBLEHUB_GPU:-auto}"
ROOTFS_DIR="${BUBBLEHUB_ROOTFS_DIR:-$INSTALL_PREFIX/rootfs/ubuntu-26.04}"
ROOTFS_SUITE="${BUBBLEHUB_ROOTFS_SUITE:-resolute}"
ROOTFS_VERSION="${BUBBLEHUB_ROOTFS_VERSION:-26.04}"
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
  echo "BubbleHub system-wide source install is Linux-only." >&2
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
  local stamp="$ROOTFS_DIR/.bubblehub-rootfs.json"
  [[ -f "$stamp" ]] &&
    grep -q "\"suite\": \"${ROOTFS_SUITE}\"" "$stamp" &&
    grep -q "\"version\": \"${ROOTFS_VERSION}\"" "$stamp"
}

echo "Building native BubbleHub core..."
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
if [[ -x /usr/local/bin/bubblehub-sandbox ]]; then
  ${SUDO} chown root:root /usr/local/bin/bubblehub-sandbox
  ${SUDO} chmod 4755 /usr/local/bin/bubblehub-sandbox
fi
if command -v ldconfig >/dev/null 2>&1; then
  ${SUDO} ldconfig
fi

echo "Building BubbleHub Python wheel..."
PY_WHEEL_DIR="$(mktemp -d)"
PY_BUILD_ENV="$(mktemp -d)"
"$PYTHON_BIN" -m venv "$PY_BUILD_ENV"
"$PY_BUILD_ENV/bin/python" -m pip install --upgrade pip build
"$PY_BUILD_ENV/bin/python" -m build --wheel --outdir "$PY_WHEEL_DIR" "$ROOT"
shopt -s nullglob
BUBBLEHUB_WHEELS=("$PY_WHEEL_DIR"/bubblehub-*.whl)
shopt -u nullglob
if [[ ${#BUBBLEHUB_WHEELS[@]} -eq 0 ]]; then
  echo "Failed to build BubbleHub wheel." >&2
  exit 1
fi
BUBBLEHUB_WHEEL="${BUBBLEHUB_WHEELS[0]}"
BUBBLEHUB_WHEEL_BASE="${BUBBLEHUB_WHEEL##*/}"
BUBBLEHUB_VERSION="${BUBBLEHUB_WHEEL_BASE#bubblehub-}"
BUBBLEHUB_VERSION="${BUBBLEHUB_VERSION%%-py3-*}"
BUBBLEHUB_VERSION="${BUBBLEHUB_VERSION%%-cp*}"
BUBBLEHUB_VERSION="${BUBBLEHUB_VERSION%%.whl}"

echo "Installing BubbleHub Python runtime into ${INSTALL_PREFIX}..."
if [[ "${BUBBLEHUB_SKIP_ROOTFS:-0}" != "1" ]] && rootfs_is_current; then
  PRESERVED_ROOTFS_PARENT="$(mktemp -d)"
  PRESERVED_ROOTFS="$PRESERVED_ROOTFS_PARENT/ubuntu-26.04"
  echo "Preserving existing BubbleHub Ubuntu rootfs for fast rebuild: ${ROOTFS_DIR}"
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
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install --no-deps "${BUBBLEHUB_WHEELS[0]}"
${SUDO} "$INSTALL_PREFIX/bin/python" -m pip install \
  --find-links "$PY_WHEEL_DIR" \
  "bubblehub[examples]==${BUBBLEHUB_VERSION}"
${SUDO} env BUBBLEHUB_GPU="$BUBBLEHUB_GPU_MODE" "$INSTALL_PREFIX/bin/python" -m bubblehub.gpu_setup \
  --mode "$BUBBLEHUB_GPU_MODE" \
  --wheel "${BUBBLEHUB_WHEELS[0]}" \
  --profile-out "$INSTALL_PREFIX/install-profile.json"
${SUDO} mv "$INSTALL_PREFIX/bin/bubble" "$INSTALL_PREFIX/bin/bubble-entrypoint"
${SUDO} mv "$INSTALL_PREFIX/bin/bubblehub" "$INSTALL_PREFIX/bin/bubblehub-entrypoint"
${SUDO} mv "$INSTALL_PREFIX/bin/bubblehub-node" "$INSTALL_PREFIX/bin/bubblehub-node-entrypoint"

echo "Linking global BubbleHub commands into ${BIN_DIR}..."
${SUDO} mkdir -p "$BIN_DIR"
${SUDO} rm -f "$BIN_DIR/bubble" "$BIN_DIR/bubblehub" "$BIN_DIR/bubblehub-node"
${SUDO} tee "$BIN_DIR/bubble" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import os, sys; from pathlib import Path; candidates = [Path(p) for p in os.environ.get("BUBBLEHUB_PYTHONPATH", "").split(os.pathsep) if p]; lib = Path(sys.prefix) / "lib"; candidates.extend(sorted(lib.glob("python*/site-packages"), reverse=True) if lib.is_dir() else []); sys.path[:0] = [str(p) for p in candidates if (p / "bubblehub").is_dir()]; sys.argv[0] = "bubble"; from bubblehub.cli.main import run_cli; run_cli()' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/bubble"
${SUDO} tee "$BIN_DIR/bubblehub" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import os, sys; from pathlib import Path; candidates = [Path(p) for p in os.environ.get("BUBBLEHUB_PYTHONPATH", "").split(os.pathsep) if p]; lib = Path(sys.prefix) / "lib"; candidates.extend(sorted(lib.glob("python*/site-packages"), reverse=True) if lib.is_dir() else []); sys.path[:0] = [str(p) for p in candidates if (p / "bubblehub").is_dir()]; sys.argv[0] = "bubblehub"; from bubblehub.cli.app import run_app; run_app()' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/bubblehub"
${SUDO} tee "$BIN_DIR/bubblehub-node" >/dev/null <<EOF
#!/usr/bin/env bash
exec "$INSTALL_PREFIX/bin/python" -I -c 'import os, sys; from pathlib import Path; candidates = [Path(p) for p in os.environ.get("BUBBLEHUB_PYTHONPATH", "").split(os.pathsep) if p]; lib = Path(sys.prefix) / "lib"; candidates.extend(sorted(lib.glob("python*/site-packages"), reverse=True) if lib.is_dir() else []); sys.path[:0] = [str(p) for p in candidates if (p / "bubblehub").is_dir()]; sys.argv[0] = "bubblehub-node"; from bubblehub.node.daemon import main; raise SystemExit(main())' "\$@"
EOF
${SUDO} chmod 0755 "$BIN_DIR/bubblehub-node"
${SUDO} ln -sf "$INSTALL_PREFIX/bin/pytest" "$BIN_DIR/pytest"
if [[ -x /usr/local/bin/bubblehub-sandbox && "$BIN_DIR/bubblehub-sandbox" != "/usr/local/bin/bubblehub-sandbox" ]]; then
  ${SUDO} ln -sf /usr/local/bin/bubblehub-sandbox "$BIN_DIR/bubblehub-sandbox"
fi

if [[ "${BUBBLEHUB_INSTALL_APP:-1}" != "0" && "${BUBBLEHUB_SKIP_TAURI:-0}" != "1" ]]; then
  if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo not found. Run ./scripts/install-deps.sh first or set BUBBLEHUB_SKIP_TAURI=1." >&2
    exit 1
  fi
  echo "Building BubbleHub Tauri desktop app..."
  "$ROOT/scripts/ci/build-tauri-app.sh"
  ${SUDO} install -m 0755 "$ROOT/app/target/release/bubblehub" "$INSTALL_PREFIX/share/bubblehub/app/bubblehub"
fi

if [[ "${BUBBLEHUB_SKIP_ROOTFS:-0}" == "1" ]]; then
  echo "Skipping BubbleHub Ubuntu rootfs because BUBBLEHUB_SKIP_ROOTFS=1."
elif rootfs_is_current; then
  echo "BubbleHub Ubuntu rootfs already exists at ${ROOTFS_DIR}; skipping rootfs creation."
else
  echo "Creating BubbleHub Ubuntu rootfs..."
  BUBBLEHUB_ROOTFS_DIR="$ROOTFS_DIR" SUDO="$SUDO" "$ROOT/scripts/create-rootfs.sh"
fi

bubblehub_run_base_model_setup

echo
echo "BubbleHub system install is ready."
echo "Run: bubble --help"
