#!/usr/bin/env bash
set -euo pipefail

VERSION_TAG="${1:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
OUTPUT_DIR="${2:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
REPO="${BUBBLEHUB_REPO:-bublhub/bubblehub}"
RUNTIME_IMAGE="${BUBBLEHUB_RUNTIME_IMAGE:-ghcr.io/${REPO}:${VERSION_TAG}}"
VERSION="${VERSION_TAG#v}"
PACKAGE_NAME="BubbleHub-${VERSION}-x64"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"

build_deb() {
  local pkg_root="$TMP_DIR/deb-root"
  local container_id

  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required to build the BubbleHub .deb package." >&2
    exit 1
  fi

  echo "Building ${PACKAGE_NAME}.deb from ${RUNTIME_IMAGE}..."
  container_id="$(docker create "$RUNTIME_IMAGE")"
  trap 'docker rm -f "$container_id" >/dev/null 2>&1 || true; cleanup' EXIT

  mkdir -p \
    "$pkg_root/DEBIAN" \
    "$pkg_root/opt" \
    "$pkg_root/usr/bin" \
    "$pkg_root/usr/lib/x86_64-linux-gnu" \
    "$pkg_root/usr/share/applications" \
    "$pkg_root/usr/share/icons/hicolor/scalable/apps" \
    "$pkg_root/usr/share/icons/hicolor/512x512/apps"

  docker cp "$container_id:/opt/bubblehub" "$pkg_root/opt/bubblehub"
  for binary in bubblehub bubblehub-node bubblehub-sandbox bubblehub-control-center llama-server; do
    docker cp "$container_id:/usr/local/bin/${binary}" "$pkg_root/usr/bin/${binary}"
  done
  docker cp "$container_id:/usr/local/lib/x86_64-linux-gnu/." "$pkg_root/usr/lib/x86_64-linux-gnu/"
  docker rm -f "$container_id" >/dev/null
  trap cleanup EXIT

  cat > "$pkg_root/DEBIAN/control" <<EOF
Package: bubblehub
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: amd64
Maintainer: BubbleHub <hello@BubbleHub.ai>
Depends: python3, libwebkit2gtk-4.1-0 | libwebkit2gtk-4.0-37, libayatana-appindicator3-1, libgtk-3-0, libxdo3, librsvg2-2, libc6, libstdc++6, libgcc-s1, libgomp1, libseccomp2
Description: Local LLM serving and sandboxed agents
 BubbleHub bundles local model serving, sandboxed agent execution, and local
 scheduling into one runtime.
EOF

  cat > "$pkg_root/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
if command -v ldconfig >/dev/null 2>&1; then
  ldconfig
fi
EOF
  chmod 0755 "$pkg_root/DEBIAN/postinst"

  cat > "$pkg_root/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
if command -v ldconfig >/dev/null 2>&1; then
  ldconfig
fi
EOF
  chmod 0755 "$pkg_root/DEBIAN/postrm"

  cat > "$pkg_root/usr/share/applications/bubblehub-control-center.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=BubbleHub Control Center
Comment=Monitor BubbleHub agents, memory, manifests, and loaded models
Exec=bubblehub app
Icon=bubblehub-control-center
Terminal=false
Categories=Development;System;Monitor;
StartupNotify=true
EOF

  cp "$pkg_root/opt/bubblehub/share/bubblehub/app/icons/bubblehub-icon.svg" "$pkg_root/usr/share/icons/hicolor/scalable/apps/bubblehub-control-center.svg"

  dpkg-deb --root-owner-group --build "$pkg_root" "$OUTPUT_DIR/${PACKAGE_NAME}.deb"
}

build_windows_exe() {
  local nsis_script="$TMP_DIR/bubblehub-installer.nsi"
  local install_url="https://github.com/${REPO}/releases/download/${VERSION_TAG}/install.ps1"

  if ! command -v makensis >/dev/null 2>&1; then
    echo "makensis is required to build ${PACKAGE_NAME}.exe. Install the nsis package." >&2
    exit 1
  fi

  echo "Building ${PACKAGE_NAME}.exe bootstrapper..."
  cat > "$nsis_script" <<EOF
Unicode true
Name "BubbleHub ${VERSION}"
OutFile "${OUTPUT_DIR}/${PACKAGE_NAME}.exe"
RequestExecutionLevel user
ShowInstDetails show

Section "Install BubbleHub"
  DetailPrint "Installing BubbleHub ${VERSION_TAG} runtime and Control Center through PowerShell and WSL..."
  StrCpy \$0 "\$TEMP\\bubblehub-install.ps1"
  FileOpen \$1 "\$0" w
  FileWrite \$1 "\$\$ErrorActionPreference = 'Stop'\$\r\$\n"
  FileWrite \$1 "\$\$env:BUBBLEHUB_VERSION = '${VERSION_TAG}'\$\r\$\n"
  FileWrite \$1 "irm '${install_url}' | iex\$\r\$\n"
  FileClose \$1
  ExecWait \`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\$0"\` \$2
  Delete "\$0"
  IntCmp \$2 0 done
    MessageBox MB_ICONSTOP "BubbleHub installer failed with exit code \$2."
    SetErrorLevel \$2
    Quit
  done:
SectionEnd
EOF

  makensis -NOCD "$nsis_script"
}

if [[ "${BUBBLEHUB_SKIP_DEB:-0}" != "1" ]]; then
  build_deb
fi

if [[ "${BUBBLEHUB_SKIP_EXE:-0}" != "1" ]]; then
  build_windows_exe
fi
