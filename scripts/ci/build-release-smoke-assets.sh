#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR_ARG="${1:-$ROOT/.ci-artifacts/release-smoke-assets}"
case "$OUT_DIR_ARG" in
  /*) OUT_DIR="$OUT_DIR_ARG" ;;
  *) OUT_DIR="$(pwd)/$OUT_DIR_ARG" ;;
esac
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

base_version() {
  python3 - "$ROOT/pyproject.toml" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'(?m)^version = "([^"]+)"', text)
if match is None:
    raise SystemExit("Could not read project version from pyproject.toml")
print(match.group(1))
PY
}

copy_source() {
  local dst="$1"

  mkdir -p "$dst"
  tar \
    --exclude='.git' \
    --exclude='.ci-artifacts' \
    --exclude='.bubblehub-cache' \
    --exclude='.openclaw-cache' \
    --exclude='app/target' \
    --exclude='libbubble/build' \
    -C "$ROOT" -cf - . | tar -C "$dst" -xf -
}

stamp_source_version() {
  local src="$1"
  local version="$2"

  python3 - "$src" "$version" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]

replacements = {
    root / "pyproject.toml": (r'(?m)^version = "[^"]+"', f'version = "{version}"'),
    root / "bubblehub" / "__init__.py": (r'__version__ = "[^"]+"', f'__version__ = "{version}"'),
    root / "app" / "Cargo.toml": (r'(?m)^version = "[^"]+"', f'version = "{version}"'),
}

for path, (pattern, replacement) in replacements.items():
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(pattern, replacement, text, count=1), encoding="utf-8")

package_json = root / "package.json"
data = json.loads(package_json.read_text(encoding="utf-8"))
data["version"] = version
package_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

safe_image_tag() {
  printf '%s' "$1" | tr '[:upper:]+/' '[:lower:]--' | tr -cd '[:alnum:]_.-'
}

write_checksums() {
  local assets_dir="$1"
  local files
  local exes

  (
    cd "$assets_dir"
    files=(
      bubblehub-source.tar.gz \
      install.sh \
      install.ps1 \
      BubbleHub-*-x64.deb \
      container-image.txt \
    )
    shopt -s nullglob
    exes=(BubbleHub-*-x64.exe)
    shopt -u nullglob
    files+=("${exes[@]}")
    sha256sum "${files[@]}" > SHA256SUMS
  )
}

write_source_tarball() {
  local src="$1"
  local version_tag="$2"
  local assets_dir="$3"

  (
    cd "$src"
    tar \
      --exclude='.git' \
      --exclude='.ci-artifacts' \
      --exclude='.bubblehub-cache' \
      --exclude='.openclaw-cache' \
      --exclude='app/target' \
      --exclude='libbubble/build' \
      --transform "s#^\./#bubblehub-${version_tag}/#" \
      -czf "$assets_dir/bubblehub-source.tar.gz" .
  )
}

build_assets_from_source() {
  local label="$1"
  local version_tag="$2"
  local assets_dir="$3"
  local src="$TMP_DIR/src-${label}"
  local image_tag
  local runtime_image
  local windows_base_url

  image_tag="$(safe_image_tag "$version_tag")"
  runtime_image="bubblehub:release-smoke-${label}-${image_tag}"
  windows_base_url="${BUBBLEHUB_RELEASE_SMOKE_WINDOWS_URL:-http://127.0.0.1:8765}"

  echo "Preparing ${label} release smoke assets for ${version_tag}..."
  copy_source "$src"
  stamp_source_version "$src" "${version_tag#v}"

  echo "Building runtime image ${runtime_image}..."
  DOCKER_BUILDKIT=1 docker build \
    --file "$src/docker/Dockerfile" \
    --target runtime \
    --tag "$runtime_image" \
    "$src"

  mkdir -p "$assets_dir"
  write_source_tarball "$src" "$version_tag" "$assets_dir"
  cp "$src/scripts/install.sh" "$assets_dir/install.sh"
  cp "$src/scripts/install.ps1" "$assets_dir/install.ps1"
  {
    echo "Image: ${runtime_image}"
    echo "Digest: local release-smoke image"
    echo
    echo "Tags:"
    echo "- ${runtime_image}"
  } > "$assets_dir/container-image.txt"

  (
    cd "$src"
    BUBBLEHUB_REPO="${BUBBLEHUB_REPO:-bublhub/bubblehub}" \
    BUBBLEHUB_RUNTIME_IMAGE="$runtime_image" \
    BUBBLEHUB_INSTALL_PS1_URL="${windows_base_url%/}/${version_tag}/install.ps1" \
      scripts/package-release.sh "$version_tag" "$assets_dir"
  )

  write_checksums "$assets_dir"
  printf '%s\n' "$version_tag" > "$assets_dir/VERSION_TAG"
}

copy_existing_current_assets() {
  local current_assets_dir="$1"
  local dst="$2"
  local version_tag="$3"

  mkdir -p "$dst"
  cp -a "$current_assets_dir/." "$dst/"
  printf '%s\n' "$version_tag" > "$dst/VERSION_TAG"
}

infer_assets_version_tag() {
  local assets_dir="$1"
  local debs
  local deb_base
  local version

  shopt -s nullglob
  debs=("$assets_dir"/BubbleHub-*-x64.deb)
  shopt -u nullglob
  if [[ ${#debs[@]} -eq 0 ]]; then
    echo "Cannot infer current version: no BubbleHub-*-x64.deb in ${assets_dir}" >&2
    exit 1
  fi
  deb_base="${debs[0]##*/}"
  version="${deb_base#BubbleHub-}"
  version="${version%-x64.deb}"
  printf 'v%s\n' "$version"
}

write_label_version() {
  local label="$1"
  local version_tag="$2"

  mkdir -p "$OUT_DIR/$label"
  printf '%s\n' "$version_tag" > "$OUT_DIR/$label/VERSION_TAG"
}

main() {
  local version_base
  local previous_version_tag
  local current_version_tag
  local current_assets_dir

  version_base="$(base_version)"
  previous_version_tag="${BUBBLEHUB_PREVIOUS_VERSION_TAG:-v${version_base}+ci.1}"
  current_version_tag="${BUBBLEHUB_CURRENT_VERSION_TAG:-v${version_base}+ci.2}"
  current_assets_dir="${BUBBLEHUB_CURRENT_ASSETS_DIR:-}"

  rm -rf "$OUT_DIR"
  mkdir -p "$OUT_DIR"

  build_assets_from_source "previous" "$previous_version_tag" "$OUT_DIR/$previous_version_tag"
  write_label_version "previous" "$previous_version_tag"
  if [[ -n "$current_assets_dir" ]]; then
    echo "Copying existing current release assets from ${current_assets_dir}..."
    current_version_tag="$(infer_assets_version_tag "$current_assets_dir")"
    copy_existing_current_assets "$current_assets_dir" "$OUT_DIR/$current_version_tag" "$current_version_tag"
  else
    build_assets_from_source "current" "$current_version_tag" "$OUT_DIR/$current_version_tag"
  fi
  write_label_version "current" "$current_version_tag"

  echo "Release smoke assets are ready in ${OUT_DIR}."
}

main "$@"
