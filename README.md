<div align="center">
  <img src="assets/ageos-logo.png" alt="AgeOS logo" width="175">
  <p>Local LLM serving and sandboxed agents in one command.</p>
  <p>
    <a href="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml/badge.svg"></a>
    <a href="https://github.com/ageos-labs/ageos-runtime/releases/latest"><img alt="GitHub release" src="https://img.shields.io/github/v/release/ageos-labs/ageos-runtime?display_name=tag"></a>
    <a href="https://discord.gg/skwKqSgvD2"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white"></a>
    <a href="LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  </p>
</div>

## Install

Linux:

```bash
curl -fsSL https://ageos.dev/install.sh | bash
```

Windows PowerShell, through WSL:

```powershell
irm https://ageos.dev/install.ps1 | iex
```

The installer downloads the latest GitHub Release artifact, installs local runtime dependencies, builds AgeOS, and links `ageos` into `/usr/local/bin`.

Check it:

```bash
ageos --help
```

Docker image:

```bash
docker pull ghcr.io/ageos-labs/ageos-runtime:latest
```

Use a release image as a base:

```dockerfile
FROM ghcr.io/ageos-labs/ageos-runtime:v0.1.0
```

## Quick Start

Ask the local model a question:

```bash
ageos prompt --text "Say hello from AgeOS"
```

Run an agent in the sandbox:

```bash
ageos run --root-dir ./examples/basic --binary ./examples/basic/basic_agent.py --memory 16G
```

Start the OpenAI-compatible local endpoint (optional):

```bash
ageos serve
```

Pick or inspect models:

```bash
ageos models
ageos models list
ageos models stop
```

## What AgeOS Does

- Runs local LLMs.
- Exposes an OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1`.
- Keeps warm model backends shared across agents.
- Runs agents in a Linux sandbox with restricted filesystem and network access.
- Injects local inference into agents as `OPENAI_BASE_URL` and `OPENAI_API_KEY`.

## Agent Environment

`ageos run` starts the shared inference endpoint before launching an agent and injects:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=ageos-local
AGEOS_API_BASE_URL=http://127.0.0.1:8000
AGEOS_SANDBOX_INFERENCE_HOST=127.0.0.1
AGEOS_SANDBOX_INFERENCE_PORT=8000
```

Sandboxed agents only get access to the local inference endpoint. General outbound network access is blocked.

When `--root-dir` is provided, the binary must live inside that root and AgeOS mounts the root as the sandbox workspace. When `--root-dir` is omitted, non-system binaries are copied into a temporary workspace before the sandbox starts. Inside the sandbox, AgeOS Python prompt/shim calls detect `AGEOS_SANDBOX=1` and use the forwarded inference endpoint instead of loading the native shared library.

## OpenClaw Example

```bash
cd examples/openclaw
ageos run --memory 16G --root-dir openclaw --binary openclaw/node_modules/.bin/openclaw tui
```

## Releases

AgeOS ships source install assets from GitHub Releases and runtime images from GHCR.

Push a `v*` tag. The release workflow runs unit tests, runs local-inference integration tests, then publishes:

- `install.sh`
- `install.ps1`
- `AgeOS-<version>-x64.deb`
- `AgeOS-<version>-x64.exe`
- `ageos-source.tar.gz`
- `container-image.txt`
- `SHA256SUMS`

For Cursor-written release notes, ask Cursor to use the AgeOS release-notes skill before tagging.
It writes `.github/releases/<tag>.md` from commits since the previous release, and the release workflow uses that file when present.

Install a specific tag:

```bash
curl -fsSL https://ageos.dev/download/linux/v0.1.0/install.sh | AGEOS_VERSION=v0.1.0 bash
```

Download a specific Debian package:

```bash
curl -LO https://ageos.dev/download/linux/v0.1.0/AgeOS-0.1.0-x64.deb
sudo apt install ./AgeOS-0.1.0-x64.deb
```

Use the matching runtime image:

```bash
docker pull ghcr.io/ageos-labs/ageos-runtime:v0.1.0
```
## Build from source

1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies and build ageos 

```base
./scripts/install-deps.sh
./scripts/build.sh
```

## Test Before Push

Run the same Docker test targets that CI uses before pushing:

```bash
docker build -f docker/Dockerfile --target unit-test -t ageos-runtime:unit .
docker run --rm --privileged --security-opt seccomp=unconfined ageos-runtime:unit
```

Integration tests also need persistent caches for the model and OpenClaw dependencies. Use Docker named volumes instead of `$PWD` bind mounts, which can fail on remote/NFS workspaces:

```bash
docker volume create ageos-cache-local
docker volume create ageos-openclaw-local

docker build -f docker/Dockerfile --target integration-test -t ageos-runtime:integration .
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v ageos-cache-local:/cache/ageos \
  -v ageos-openclaw-local:/cache/openclaw \
  ageos-runtime:integration
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
