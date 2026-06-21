# syntax=docker/dockerfile:1.7

ARG UBUNTU_VERSION=22.04

FROM ubuntu:${UBUNTU_VERSION} AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
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
      python3-pip \
      python3-venv \
      sudo \
      xz-utils && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --upgrade pip setuptools wheel

WORKDIR /src

FROM base AS llama

ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git
ARG LLAMA_CPP_REF=master

RUN --mount=type=cache,target=/root/.cache/ccache \
    git clone --depth 1 --branch "${LLAMA_CPP_REF}" "${LLAMA_CPP_REPO}" /tmp/ageos-llama.cpp && \
    cmake -S /tmp/ageos-llama.cpp -B /tmp/ageos-llama.cpp/build -DLLAMA_CURL=OFF && \
    cmake --build /tmp/ageos-llama.cpp/build --target llama-server --parallel "$(nproc)" && \
    install -m 0755 /tmp/ageos-llama.cpp/build/bin/llama-server /usr/local/bin/llama-server && \
    mkdir -p /usr/local/lib/x86_64-linux-gnu && \
    find /tmp/ageos-llama.cpp/build -name 'lib*.so*' -exec cp -a --remove-destination {} /usr/local/lib/x86_64-linux-gnu/ \; && \
    ldconfig && \
    rm -rf /tmp/ageos-llama.cpp

FROM base AS source

COPY . /src

FROM source AS unit-test

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install ".[dev]" && \
    rm -rf libageos/build && \
    meson setup libageos/build libageos --prefix=/usr/local && \
    meson compile -C libageos/build

CMD ["scripts/ci/run-unit-tests.sh"]

FROM llama AS rootfs

COPY scripts/create-rootfs.sh /src/scripts/create-rootfs.sh

RUN SUDO= /src/scripts/create-rootfs.sh

FROM rootfs AS runtime

COPY . /src

RUN --mount=type=cache,target=/root/.cache/pip \
    SUDO= ./scripts/build.sh && \
    ageos --help

CMD ["ageos", "--help"]

FROM runtime AS integration-test

ENV AGEOS_RUN_INTEGRATION=1 \
    AGEOS_CACHE=/cache/ageos \
    AGEOS_MODELS_CONFIG=/cache/ageos/ci-models.yaml \
    AGEOS_INTEGRATION_WORKSPACE_DIR=/cache/ageos/integration-workspaces \
    AGEOS_LLAMA_CTX_SIZE=512 \
    AGEOS_MAX_OUTPUT_TOKENS=32 \
    NO_PROXY=127.0.0.1,localhost \
    OPENCLAW_CACHE_DIR=/cache/openclaw

CMD ["scripts/ci/run-integration-tests.sh"]
