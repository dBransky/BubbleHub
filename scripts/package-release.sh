#!/usr/bin/env bash
set -euo pipefail

VERSION_TAG="${1:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
OUTPUT_DIR="${2:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
REPO="${AGEOS_REPO:-ageos-labs/ageos-runtime}"
RUNTIME_IMAGE="${AGEOS_RUNTIME_IMAGE:-ghcr.io/${REPO}:${VERSION_TAG}}"
VERSION="${VERSION_TAG#v}"
PACKAGE_NAME="AgeOS-${VERSION}-x64"
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
    echo "docker is required to build the AgeOS .deb package." >&2
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
    "$pkg_root/usr/share/icons/hicolor/512x512/apps"

  docker cp "$container_id:/opt/ageos" "$pkg_root/opt/ageos"
  for binary in ageos ageos-node ageos-sandbox ageos-control-center llama-server; do
    docker cp "$container_id:/usr/local/bin/${binary}" "$pkg_root/usr/bin/${binary}"
  done
  docker cp "$container_id:/usr/local/lib/x86_64-linux-gnu/." "$pkg_root/usr/lib/x86_64-linux-gnu/"
  docker rm -f "$container_id" >/dev/null
  trap cleanup EXIT

  cat > "$pkg_root/DEBIAN/control" <<EOF
Package: ageos
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: amd64
Maintainer: AgeOS <hello@ageos-labs.com>
Depends: python3, libwebkit2gtk-4.1-0 | libwebkit2gtk-4.0-37, libayatana-appindicator3-1, libgtk-3-0, libxdo3, librsvg2-2, libc6, libstdc++6, libgcc-s1, libgomp1, libseccomp2
Description: Local LLM serving and sandboxed agents
 AgeOS bundles local model serving, sandboxed agent execution, and local
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

  cat > "$pkg_root/usr/share/applications/ageos-control-center.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=AgeOS Control Center
Comment=Monitor AgeOS agents, memory, manifests, and loaded models
Exec=ageos app
Icon=ageos-control-center
Terminal=false
Categories=Development;System;Monitor;
StartupNotify=true
EOF

  cp "$pkg_root/opt/ageos/share/ageos/app/icons/ageos-icon.png" "$pkg_root/usr/share/icons/hicolor/512x512/apps/ageos-control-center.png"

  dpkg-deb --root-owner-group --build "$pkg_root" "$OUTPUT_DIR/${PACKAGE_NAME}.deb"
}

build_windows_exe() {
  local nsis_script="$TMP_DIR/ageos-installer.nsi"
  local install_url="https://github.com/${REPO}/releases/download/${VERSION_TAG}/install.ps1"

  if ! command -v makensis >/dev/null 2>&1; then
    echo "makensis is required to build ${PACKAGE_NAME}.exe. Install the nsis package." >&2
    exit 1
  fi

  echo "Building ${PACKAGE_NAME}.exe bootstrapper..."
  cat > "$nsis_script" <<EOF
Unicode true
Name "AgeOS ${VERSION}"
OutFile "${OUTPUT_DIR}/${PACKAGE_NAME}.exe"
RequestExecutionLevel user
ShowInstDetails show

Section "Install AgeOS"
  DetailPrint "Installing AgeOS ${VERSION_TAG} runtime and Control Center through PowerShell and WSL..."
  StrCpy \$0 "\$TEMP\\ageos-install.ps1"
  FileOpen \$1 "\$0" w
  FileWrite \$1 "\$\$ErrorActionPreference = 'Stop'\$\r\$\n"
  FileWrite \$1 "\$\$env:AGEOS_VERSION = '${VERSION_TAG}'\$\r\$\n"
  FileWrite \$1 "irm '${install_url}' | iex\$\r\$\n"
  FileClose \$1
  ExecWait \`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\$0"\` \$2
  Delete "\$0"
  IntCmp \$2 0 done
    MessageBox MB_ICONSTOP "AgeOS installer failed with exit code \$2."
    SetErrorLevel \$2
    Quit
  done:
SectionEnd
EOF

  makensis -NOCD "$nsis_script"
}

if [[ "${AGEOS_SKIP_DEB:-0}" != "1" ]]; then
  build_deb
fi

if [[ "${AGEOS_SKIP_EXE:-0}" != "1" ]]; then
  build_windows_exe
fi
