#!/usr/bin/env bash
set -euo pipefail

METHOD="${1:?usage: scripts/ci/run-linux-release-install-smoke.sh <curl|apt> <release-smoke-assets-dir>}"
ASSETS_DIR="${2:?usage: scripts/ci/run-linux-release-install-smoke.sh <curl|apt> <release-smoke-assets-dir>}"
PORT="${BUBBLEHUB_RELEASE_SMOKE_PORT:-8765}"
HOST_BASE_URL="${BUBBLEHUB_RELEASE_SMOKE_HOST_URL:-http://127.0.0.1:${PORT}}"
CONTAINER_BASE_URL="${BUBBLEHUB_RELEASE_SMOKE_CONTAINER_URL:-http://host.docker.internal:${PORT}}"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing required release smoke asset: $path" >&2
    exit 1
  fi
}

wait_for_server() {
  local url="$1"

  for _ in {1..30}; do
    if curl -fsSL "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "Timed out waiting for artifact server at ${url}" >&2
  exit 1
}

start_server() {
  python3 -m http.server "$PORT" --bind 0.0.0.0 --directory "$ASSETS_DIR" &
  SERVER_PID="$!"
  wait_for_server "${HOST_BASE_URL}/previous/VERSION_TAG"
}

run_container() {
  local previous_tag="$1"
  local current_tag="$2"
  local script

  case "$METHOD" in
    curl)
      script='
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        export TZ=Etc/UTC
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl sudo python3

        install_version() {
          local version_tag="$1"
          echo "--- curl installer smoke: ${version_tag} ---"
          curl -fsSL "${BASE_URL}/${version_tag}/install.sh" \
            | BUBBLEHUB_VERSION="${version_tag}" \
              DEBIAN_FRONTEND=noninteractive \
              TZ=Etc/UTC \
              BUBBLEHUB_RELEASE_BASE_URL="${BASE_URL}" \
              BUBBLEHUB_SKIP_MODEL_SETUP=1 \
              BUBBLEHUB_SKIP_ROOTFS="${BUBBLEHUB_SKIP_ROOTFS:-1}" \
              BUBBLEHUB_INSTALL_APP=0 \
              bash
          verify_install "${version_tag}" "curl"
        }

        verify_install() {
          local version_tag="$1"
          local install_method="$2"
          local version="${version_tag#v}"
          local actual_version
          local health="/tmp/bubblehub-health.json"
          local log="/tmp/bubblehub.log"
          local specialties="/tmp/bubblehub-specialties.txt"
          local app_pid=""

          command -v bubble >/dev/null
          command -v bubblehub >/dev/null
          command -v bubblehub-node >/dev/null
          command -v bubblehub-sandbox >/dev/null
          echo "--- validating Bubble CLI ${version} (${install_method}) ---"
          actual_version="$(bubble --version)"
          echo "$actual_version"
          test "$actual_version" = "bubble ${version}"
          bubble --help >/dev/null
          bubble specialties list | tee "$specialties"
          grep -q "^default-instruct" "$specialties"

          if [[ "$install_method" == "apt" ]]; then
            test "$(dpkg-query -W -f=\${Version} bubblehub)" = "$version"
            command -v llama-server >/dev/null
            test -x /opt/bubblehub/share/bubblehub/app/bubblehub
            test -f /usr/share/applications/bubblehub.desktop
            test -f /usr/share/icons/hicolor/scalable/apps/bubblehub.svg
          fi

          bubblehub --server-only --port 18010 >"$log" 2>&1 &
          app_pid="$!"
          for _ in $(seq 1 30); do
            if curl -fsSL http://127.0.0.1:18010/health >"$health" 2>/dev/null; then
              break
            fi
            sleep 1
          done
          if [[ ! -s "$health" ]]; then
            cat "$log" >&2 || true
            exit 1
          fi
          grep -q "\"service\": \"bubblehub\"" "$health"
          grep -q "\"version\": \"${version}\"" "$health"
          kill "$app_pid" >/dev/null 2>&1 || true
          wait "$app_pid" >/dev/null 2>&1 || true
        }

        install_version "$PREVIOUS_TAG"
        install_version "$CURRENT_TAG"
      '
      ;;
    apt)
      script='
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        export TZ=Etc/UTC
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl python3

        install_version() {
          local version_tag="$1"
          local version="${version_tag#v}"
          local deb="/tmp/BubbleHub-${version}-x64.deb"
          echo "--- apt package smoke: ${version_tag} ---"
          curl -fsSL "${BASE_URL}/${version_tag}/BubbleHub-${version}-x64.deb" -o "$deb"
          apt-get install -y "$deb"
          verify_install "${version_tag}" "apt"
        }

        verify_install() {
          local version_tag="$1"
          local install_method="$2"
          local version="${version_tag#v}"
          local actual_version
          local health="/tmp/bubblehub-health.json"
          local log="/tmp/bubblehub.log"
          local specialties="/tmp/bubblehub-specialties.txt"
          local app_pid=""

          command -v bubble >/dev/null
          command -v bubblehub >/dev/null
          command -v bubblehub-node >/dev/null
          command -v bubblehub-sandbox >/dev/null
          echo "--- validating Bubble CLI ${version} (${install_method}) ---"
          actual_version="$(bubble --version)"
          echo "$actual_version"
          test "$actual_version" = "bubble ${version}"
          bubble --help >/dev/null
          bubble specialties list | tee "$specialties"
          grep -q "^default-instruct" "$specialties"

          if [[ "$install_method" == "apt" ]]; then
            test "$(dpkg-query -W -f=\${Version} bubblehub)" = "$version"
            command -v llama-server >/dev/null
            test -x /opt/bubblehub/share/bubblehub/app/bubblehub
            test -f /usr/share/applications/bubblehub.desktop
            test -f /usr/share/icons/hicolor/scalable/apps/bubblehub.svg
          fi

          bubblehub --server-only --port 18010 >"$log" 2>&1 &
          app_pid="$!"
          for _ in $(seq 1 30); do
            if curl -fsSL http://127.0.0.1:18010/health >"$health" 2>/dev/null; then
              break
            fi
            sleep 1
          done
          if [[ ! -s "$health" ]]; then
            cat "$log" >&2 || true
            exit 1
          fi
          grep -q "\"service\": \"bubblehub\"" "$health"
          grep -q "\"version\": \"${version}\"" "$health"
          kill "$app_pid" >/dev/null 2>&1 || true
          wait "$app_pid" >/dev/null 2>&1 || true
        }

        install_version "$PREVIOUS_TAG"
        install_version "$CURRENT_TAG"
      '
      ;;
    *)
      echo "Unknown Linux release install smoke method: ${METHOD}" >&2
      exit 1
      ;;
  esac

  docker run --rm \
    --privileged \
    --security-opt seccomp=unconfined \
    --add-host=host.docker.internal:host-gateway \
    -e BASE_URL="$CONTAINER_BASE_URL" \
    -e PREVIOUS_TAG="$previous_tag" \
    -e CURRENT_TAG="$current_tag" \
    -e BUBBLEHUB_SKIP_ROOTFS="${BUBBLEHUB_RELEASE_SMOKE_SKIP_ROOTFS:-1}" \
    ubuntu:22.04 \
    bash -lc "$script"
}

main() {
  local previous_tag
  local current_tag

  require_file "$ASSETS_DIR/previous/VERSION_TAG"
  require_file "$ASSETS_DIR/current/VERSION_TAG"
  previous_tag="$(tr -d '[:space:]' < "$ASSETS_DIR/previous/VERSION_TAG")"
  current_tag="$(tr -d '[:space:]' < "$ASSETS_DIR/current/VERSION_TAG")"
  require_file "$ASSETS_DIR/$previous_tag/install.sh"
  require_file "$ASSETS_DIR/$current_tag/install.sh"

  start_server
  run_container "$previous_tag" "$current_tag"
}

main "$@"
