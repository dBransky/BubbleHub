#!/usr/bin/env bash
set -euo pipefail

VERSION_TAG="${1:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
OUTPUT_DIR="${2:?usage: scripts/package-release.sh <version-tag> <output-dir>}"
REPO="${BUBBLEHUB_REPO:-bublhub/bubblehub}"
RUNTIME_IMAGE="${BUBBLEHUB_RUNTIME_IMAGE:-ghcr.io/${REPO}:${VERSION_TAG}}"
VERSION="${VERSION_TAG#v}"
PACKAGE_NAME="BubbleHub-${VERSION}-x64"
TMP_DIR="$(mktemp -d)"
DEB_CONTAINER_ID=""

cleanup() {
  if [[ -n "$DEB_CONTAINER_ID" ]]; then
    docker rm -f "$DEB_CONTAINER_ID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$OUTPUT_DIR"

build_deb() {
  local pkg_root="$TMP_DIR/deb-root"

  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required to build the BubbleHub .deb package." >&2
    exit 1
  fi

  echo "Building ${PACKAGE_NAME}.deb from ${RUNTIME_IMAGE}..."
  DEB_CONTAINER_ID="$(docker create "$RUNTIME_IMAGE")"

  mkdir -p \
    "$pkg_root/DEBIAN" \
    "$pkg_root/opt" \
    "$pkg_root/usr/bin" \
    "$pkg_root/usr/lib/x86_64-linux-gnu" \
    "$pkg_root/usr/share/applications" \
    "$pkg_root/usr/share/icons/hicolor/scalable/apps" \
    "$pkg_root/usr/share/icons/hicolor/512x512/apps"

  docker cp "$DEB_CONTAINER_ID:/opt/bubblehub" - | tar \
    --no-same-owner \
    --no-same-permissions \
    --exclude='bubblehub/rootfs/ubuntu-26.04/dev/*' \
    -C "$pkg_root/opt" \
    -xf -
  mkdir -p "$pkg_root/opt/bubblehub/rootfs/ubuntu-26.04/dev"
  for binary in bubble bubblehub bubblehub-node bubblehub-sandbox llama-server; do
    docker cp "$DEB_CONTAINER_ID:/usr/local/bin/${binary}" - | tar --no-same-owner --no-same-permissions -C "$pkg_root/usr/bin" -xf -
  done
  chmod 0755 "$pkg_root/usr/bin"/bubble "$pkg_root/usr/bin"/bubblehub "$pkg_root/usr/bin"/bubblehub-node "$pkg_root/usr/bin"/llama-server
  chmod 4755 "$pkg_root/usr/bin/bubblehub-sandbox"
  docker cp "$DEB_CONTAINER_ID:/usr/local/lib/x86_64-linux-gnu/." - | tar --no-same-owner --no-same-permissions -C "$pkg_root/usr/lib/x86_64-linux-gnu" -xf -
  docker rm -f "$DEB_CONTAINER_ID" >/dev/null
  DEB_CONTAINER_ID=""

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

  cat > "$pkg_root/usr/share/applications/bubblehub.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=BubbleHub
Comment=Monitor BubbleHub agents, memory, manifests, and loaded models
Exec=bubblehub
Icon=bubblehub
Terminal=false
Categories=Development;System;Monitor;
StartupNotify=true
EOF

  cp "$pkg_root/opt/bubblehub/share/bubblehub/app/icons/bubblehub-icon.svg" "$pkg_root/usr/share/icons/hicolor/scalable/apps/bubblehub.svg"

  dpkg-deb --root-owner-group --build "$pkg_root" "$OUTPUT_DIR/${PACKAGE_NAME}.deb"
}

build_windows_exe() {
  local nsis_script="$TMP_DIR/bubblehub-installer.nsi"
  local install_script="${BUBBLEHUB_INSTALL_PS1_PATH:-scripts/install.ps1}"
  local release_base_url="${BUBBLEHUB_RELEASE_BASE_URL:-}"
  local icon_path="$TMP_DIR/bubblehub.ico"

  if ! command -v makensis >/dev/null 2>&1; then
    echo "makensis is required to build ${PACKAGE_NAME}.exe. Install the nsis package." >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to generate the BubbleHub Windows installer icon." >&2
    exit 1
  fi
  if ! command -v rsvg-convert >/dev/null 2>&1; then
    echo "rsvg-convert is required to render assets/bubblehub-logo.svg for the Windows installer icon. Install librsvg2-bin." >&2
    exit 1
  fi
  if [[ ! -f "$install_script" ]]; then
    echo "Windows installer script not found: ${install_script}" >&2
    exit 1
  fi

  python3 scripts/ci/write-windows-icon.py "$icon_path"

  echo "Building ${PACKAGE_NAME}.exe bootstrapper..."
  cat > "$nsis_script" <<EOF
Unicode true
Target amd64-unicode
Name "BubbleHub ${VERSION}"
OutFile "${OUTPUT_DIR}/${PACKAGE_NAME}.exe"
Icon "${icon_path}"
RequestExecutionLevel user
ShowInstDetails show
BrandingText "BubbleHub Control Center BUBBLEHUB_BUNDLED_INSTALL_PS1"
!define MUI_ICON "${icon_path}"
!define MUI_UNICON "${icon_path}"
!include MUI2.nsh
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install BubbleHub"
  DetailPrint "Installing BubbleHub ${VERSION_TAG} runtime and Control Center through PowerShell and WSL..."
  InitPluginsDir
  SetOutPath "\$PLUGINSDIR"
  File /oname=install.ps1 "${install_script}"
  StrCpy \$0 "\$TEMP\\bubblehub-install-bootstrap.ps1"
  FileOpen \$1 "\$0" w
  FileWrite \$1 "\$\$ErrorActionPreference = 'Stop'\$\r\$\n"
  FileWrite \$1 "\$\$LogPath = \$\$env:BUBBLEHUB_INSTALLER_LOG\$\r\$\n"
  FileWrite \$1 "if (\$\$LogPath) { Start-Transcript -Path \$\$LogPath -Append | Out-Null }\$\r\$\n"
  FileWrite \$1 "try {\$\r\$\n"
  FileWrite \$1 "\$\$env:BUBBLEHUB_VERSION = '${VERSION_TAG}'\$\r\$\n"
  FileWrite \$1 "\$\$env:BUBBLEHUB_BUNDLED_INSTALL_PS1 = '1'\$\r\$\n"
  IfSilent silent_mode normal_mode
  silent_mode:
    FileWrite \$1 "\$\$env:BUBBLEHUB_INSTALLER_SILENT = '1'\$\r\$\n"
    Goto after_silent_mode
  normal_mode:
    FileWrite \$1 "if (-not \$\$env:BUBBLEHUB_INSTALLER_SILENT) { \$\$env:BUBBLEHUB_INSTALLER_SILENT = '0' }\$\r\$\n"
  after_silent_mode:
EOF

  if [[ -n "$release_base_url" ]]; then
    cat >> "$nsis_script" <<EOF
  FileWrite \$1 "\$\$env:BUBBLEHUB_RELEASE_BASE_URL = '${release_base_url%/}'\$\r\$\n"
EOF
  fi

  cat >> "$nsis_script" <<EOF
  FileWrite \$1 "& '\$PLUGINSDIR\\install.ps1'\$\r\$\n"
  FileWrite \$1 "} finally { if (\$\$LogPath) { Stop-Transcript | Out-Null } }\$\r\$\n"
  FileClose \$1
  ExecWait \`powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "\$0"\` \$2
  Delete "\$0"
  IntCmp \$2 0 done
    IfSilent silent_failure
    MessageBox MB_ICONSTOP "BubbleHub installer failed with exit code \$2."
  silent_failure:
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
