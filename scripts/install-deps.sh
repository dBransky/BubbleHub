#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "BubbleHub runtime dependencies are Linux-focused. Install Python deps with pip on this platform."
  exit 0
fi

export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export TZ="${TZ:-Etc/UTC}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/install-ui.sh"
BUBBLEHUB_INSTALL_APP="$(bubblehub_resolve_desktop_app_choice)"
export BUBBLEHUB_INSTALL_APP
if [[ "${BUBBLEHUB_SKIP_TAURI:-0}" == "1" ]]; then
  export BUBBLEHUB_SKIP_TAURI=1
elif [[ "$BUBBLEHUB_INSTALL_APP" == "1" ]]; then
  export BUBBLEHUB_SKIP_TAURI=0
else
  export BUBBLEHUB_SKIP_TAURI=1
fi

sudo -E apt-get update
sudo -E apt-get install -y \
  build-essential \
  cmake \
  curl \
  debootstrap \
  git \
  libseccomp-dev \
  meson \
  ninja-build \
  pkg-config \
  python3-dev \
  python3-full \
  python3-gi \
  python3-pip \
  python3-venv

if [[ "${BUBBLEHUB_INSTALL_APP:-1}" != "0" && "${BUBBLEHUB_SKIP_TAURI:-0}" != "1" ]]; then
  bash "$SCRIPT_DIR/install-app-deps.sh"
fi

if [[ "${BUBBLEHUB_SKIP_MODEL_SETUP:-0}" == "1" ]]; then
  echo "Skipping llama.cpp server setup because BUBBLEHUB_SKIP_MODEL_SETUP=1."
  echo "BubbleHub GPU mode setup skipped."
  exit 0
fi

LLAMA_CPP_REPO="${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}"
LLAMA_CPP_REF="${LLAMA_CPP_REF:-master}"
LLAMA_CPP_SRC="${LLAMA_CPP_SRC:-/tmp/bubblehub-llama.cpp}"
LLAMA_CPP_BUILD="$LLAMA_CPP_SRC/build"
LLAMA_LIB_DIR="/usr/local/lib/x86_64-linux-gnu"
BUBBLEHUB_GPU_MODE="${BUBBLEHUB_GPU:-auto}"

detect_llama_gpu_backend() {
  case "$BUBBLEHUB_GPU_MODE" in
    cpu)
      echo "cpu"
      return
      ;;
    cuda-llama|rocm-llama|vulkan-llama|sycl-llama)
      echo "$BUBBLEHUB_GPU_MODE"
      return
      ;;
    vllm)
      echo "cpu"
      return
      ;;
    auto)
      ;;
    *)
      echo "Unsupported BUBBLEHUB_GPU mode: $BUBBLEHUB_GPU_MODE" >&2
      exit 1
      ;;
  esac

  if command -v nvidia-smi >/dev/null 2>&1; then
    echo "cuda-llama"
    return
  fi
  if command -v rocm-smi >/dev/null 2>&1 || command -v rocminfo >/dev/null 2>&1; then
    echo "rocm-llama"
    return
  fi
  if command -v sycl-ls >/dev/null 2>&1 && sycl-ls 2>/dev/null | grep -qi gpu; then
    echo "sycl-llama"
    return
  fi
  if command -v vulkaninfo >/dev/null 2>&1 && vulkaninfo --summary 2>/dev/null | grep -q deviceName; then
    echo "vulkan-llama"
    return
  fi
  echo "cpu"
}

llama_cmake_args() {
  local backend="$1"
  local args=(-DLLAMA_CURL=OFF)
  case "$backend" in
    cuda-llama)
      args+=(-DGGML_CUDA=ON)
      ;;
    rocm-llama)
      args+=(-DGGML_HIP=ON)
      ;;
    vulkan-llama)
      args+=(-DGGML_VULKAN=ON)
      ;;
    sycl-llama)
      args+=(-DGGML_SYCL=ON)
      ;;
    cpu)
      ;;
    *)
      echo "Unknown llama.cpp backend: $backend" >&2
      exit 1
      ;;
  esac
  printf '%s\n' "${args[@]}"
}

build_llama_server() {
  local backend="$1"
  local -a cmake_args
  mapfile -t cmake_args < <(llama_cmake_args "$backend")

  rm -rf "$LLAMA_CPP_SRC"
  git clone --depth 1 --branch "$LLAMA_CPP_REF" "$LLAMA_CPP_REPO" "$LLAMA_CPP_SRC"
  echo "Configuring llama.cpp backend: $backend"
  cmake -S "$LLAMA_CPP_SRC" -B "$LLAMA_CPP_BUILD" "${cmake_args[@]}"
  cmake --build "$LLAMA_CPP_BUILD" --target llama-server --parallel "$(nproc)"
}

LLAMA_BACKEND="$(detect_llama_gpu_backend)"
CURRENT_LLAMA_BACKEND="missing"
if command -v llama-server >/dev/null 2>&1; then
  CURRENT_LLAMA_BACKEND="$(cat /usr/local/share/bubblehub/llama-backend 2>/dev/null || echo unknown)"
fi

if ! command -v llama-server >/dev/null 2>&1 || [[ "$LLAMA_BACKEND" != "cpu" && "$CURRENT_LLAMA_BACKEND" != "$LLAMA_BACKEND" ]]; then
  echo "Installing llama.cpp server from ${LLAMA_CPP_REPO} (${LLAMA_CPP_REF})..."
  if ! build_llama_server "$LLAMA_BACKEND"; then
    if [[ "$BUBBLEHUB_GPU_MODE" == "auto" && "$LLAMA_BACKEND" != "cpu" ]]; then
      echo "GPU llama.cpp build failed for ${LLAMA_BACKEND}; retrying CPU build..." >&2
      build_llama_server "cpu"
      LLAMA_BACKEND="cpu"
    else
      echo "Failed to build llama.cpp backend ${LLAMA_BACKEND}." >&2
      exit 1
    fi
  fi
  sudo install -m 0755 "$LLAMA_CPP_BUILD/bin/llama-server" /usr/local/bin/llama-server
  sudo mkdir -p /usr/local/share/bubblehub
  echo "$LLAMA_BACKEND" | sudo tee /usr/local/share/bubblehub/llama-backend >/dev/null
else
  echo "llama-server already available at $(command -v llama-server) (${CURRENT_LLAMA_BACKEND})"
fi
if [[ -d "$LLAMA_CPP_BUILD" ]]; then
  sudo mkdir -p "$LLAMA_LIB_DIR"
  find "$LLAMA_CPP_BUILD" -name 'lib*.so*' -exec sudo cp -a --remove-destination {} "$LLAMA_LIB_DIR"/ \;
  sudo ldconfig
fi

echo "BubbleHub GPU mode: ${BUBBLEHUB_GPU_MODE}. Optional vLLM dependencies are installed by scripts/build.sh when supported."
