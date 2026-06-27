#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.cargo/bin:${PATH}"

install_rustup() {
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
  export PATH="${HOME}/.cargo/bin:${PATH}"
}

if command -v rustup >/dev/null 2>&1; then
  rustup toolchain install stable --profile minimal
  rustup default stable
elif [[ -x "${HOME}/.cargo/bin/rustup" ]]; then
  "${HOME}/.cargo/bin/rustup" toolchain install stable --profile minimal
  "${HOME}/.cargo/bin/rustup" default stable
else
  install_rustup
fi

rustc --version
cargo --version
