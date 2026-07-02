<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/bubblehub-logo-dark.svg">
    <img src="assets/bubblehub-logo.svg" alt="BubbleHub logo" width="250">
  </picture>
  <p>Local LLM serving and sandboxed agents in one command.</p>
  <p>
    <a href="https://github.com/bublhub/BubbleHub/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/bublhub/BubbleHub/actions/workflows/ci.yml/badge.svg"></a>
    <a href="https://codecov.io/gh/bublhub/BubbleHub"><img alt="Coverage" src="https://codecov.io/gh/bublhub/BubbleHub/graph/badge.svg"></a>
    <a href="https://github.com/bublhub/BubbleHub/releases/latest"><img alt="GitHub release" src="https://img.shields.io/github/v/release/bublhub/BubbleHub?display_name=tag"></a>
    <a href="https://discord.gg/skwKqSgvD2"><img alt="Discord" src="https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white"></a>
    <a href="LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue.svg"></a>
  </p>
</div>

## Install

Linux:

```bash
curl -fsSL https://bubblehub.ai/install.sh | bash
```

Windows PowerShell, through WSL:

```powershell
irm https://bubblehub.ai/install.ps1 | iex
```

Check it:

```bash
bubblehub --help
```

Open the app:

```bash
bubblehub app
```

Docker image:

```bash
docker pull ghcr.io/bublhub/bubblehub:latest
```

Use a release image as a base:

```dockerfile
FROM ghcr.io/bublhub/bubblehub:v0.1.0
```

## Quick Start

Ask the local model a question:

```bash
bubblehub prompt --text "Say hello from BubbleHub"
```

Run an agent in the sandbox:

```bash
bubblehub run --root-dir ./examples/basic --binary ./examples/basic/basic_agent.py --memory 16G
```

Name an agent for `bubblehub ps`, the shell prompt, and the Control Center:

```bash
bubblehub shell --name reviewer --root-dir ./workspace
bubblehub ps --kill agt-...
```

Start the OpenAI-compatible local endpoint (optional):

```bash
bubblehub serve
```

Pick or inspect models:

```bash
bubblehub models
bubblehub models list
bubblehub models stop
```

Open app:

```bash
bubblehub app
```

## What BubbleHub Does

- Runs local LLMs.
- Exposes an OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1`.
- Keeps warm model backends shared across agents.
- Runs agents in a Linux sandbox with restricted filesystem and network access.
- Injects local inference into agents as `OPENAI_BASE_URL` and `OPENAI_API_KEY`.
- Provides the BubbleHub Control Center desktop app for graphical monitoring, manifest review, and base-model selection.

## Agent Environment

`bubblehub run` starts the shared inference endpoint before launching an agent and injects:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=bubblehub-local
BUBBLEHUB_API_BASE_URL=http://127.0.0.1:8000
BUBBLEHUB_SANDBOX_INFERENCE_HOST=127.0.0.1
BUBBLEHUB_SANDBOX_INFERENCE_PORT=8000
```

Sandboxed agents only get access to the local inference endpoint by default. HTTP clients see a loopback policy proxy at `http://127.0.0.1:18080`, and BubbleHub injects `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy` for isolated sandboxes. The proxy is owned by `libbubblehub`: it checks the persistent sandbox access manifest under `~/.local/state/bubblehub/sandboxes/<agent-id>/access-manifest.json` (or `BUBBLEHUB_STATE_DIR`) and either forwards allowed requests or returns a logged `403`. Unknown requests fail closed; when no host prompt is available, BubbleHub records them as pending in the manifest for later dashboard review. Use `--allow-network` with `bubblehub run` or `bubblehub shell` when an agent setup step needs general outbound network access.

When `bubblehub run` or `bubblehub shell` is connected to a real terminal, first access to a new host pauses the agent and prompts on the host for `always`, `never`, or `ask every time (approve now)`. Non-interactive runs fail closed and print a reminder to run `bubblehub dashboard`; the dashboard resolves pending sandbox access requests before the live resource view opens. Use `bubblehub manifest --root-dir <dir>` or `bubblehub manifest --agent-id <agent>` to inspect and edit persisted policies. Manifest policies are exactly `always`, `never`, or `ask`; HTTP policies match domains plus the visible HTTP method/path, while HTTPS `CONNECT` can only be matched at the host/port level.

When `--root-dir` is provided, non-system binaries must live inside that root and BubbleHub mounts the root as the sandbox workspace. System binaries from `/usr`, `/bin`, `/sbin`, or `/opt/bubblehub` can still be used with a root directory, which lets `bubblehub shell --root-dir <dir>` open a shell inside the workspace sandbox. When `--root-dir` is omitted, non-system binaries are copied into a temporary workspace before the sandbox starts. Inside the sandbox, BubbleHub Python prompt/shim calls detect `BUBBLEHUB_SANDBOX=1` and use the forwarded inference endpoint instead of loading the native shared library.

Installed sandboxes run over an Ubuntu 26.04 root filesystem using a per-agent overlay. The Ubuntu lower filesystem stays unchanged; writes outside the workspace copy up into `.bubblehub/agents/<agent-id>/overlay/upper` and persist with that agent. Use `--force-new-sandbox` or `--overwrite-sandbox` to discard the persistent home and private overlay for the current workspace.

For implementation details, security assumptions, and known gaps, see [`docs/sandbox.md`](docs/sandbox.md).

## OpenClaw Example

OpenClaw can be installed entirely from inside the sandbox. The persistent agent home keeps `nvm`, npm global packages, and OpenClaw config across runs.

```bash
bubblehub shell --allow-network --root-dir openclaw
```

Inside the sandbox shell:

```bash
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.5/install.sh | bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm install 22.19.0
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

## Releases

BubbleHub ships source install assets from GitHub Releases and runtime images from GHCR.

Push a `v*` tag. The release workflow runs unit tests, runs local-inference integration tests, then publishes:

- `install.sh`
- `install.ps1`
- `BubbleHub-<version>-x64.deb`
- `BubbleHub-<version>-x64.exe`
- `bubblehub-source.tar.gz`
- `container-image.txt`
- `SHA256SUMS`

For Cursor-written release notes, ask Cursor to use the BubbleHub release-notes skill before tagging.
It writes `.github/releases/<tag>.md` from commits since the previous release, and the release workflow uses that file when present.

Install a specific tag:

```bash
curl -fsSL https://bubblehub.ai/download/linux/v0.1.0/install.sh | BUBBLEHUB_VERSION=v0.1.0 bash
```

Download a specific Debian package:

```bash
curl -LO https://bubblehub.ai/download/linux/v0.1.0/BubbleHub-0.1.0-x64.deb
sudo apt install ./BubbleHub-0.1.0-x64.deb
```

Use the matching runtime image:

```bash
docker pull ghcr.io/bublhub/bubblehub:v0.1.0
```
## Build from source

1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies and build BubbleHub. The build installs the native `bubblehub-sandbox` helper under `/usr/local/bin` with the permissions required for sandbox setup, creates the Ubuntu 26.04 rootfs on first install, and preserves it on later local rebuilds for a faster development loop.

```bash
./scripts/install-deps.sh
./scripts/build.sh
```

## Test Before Push

Run the same Docker test targets that CI uses before pushing:

```bash
docker build -f docker/Dockerfile --target unit-test -t bubblehub:unit .
docker run --rm --privileged --security-opt seccomp=unconfined bubblehub:unit
```

CI enforces line coverage through [Codecov](https://codecov.io/gh/bublhub/BubbleHub) (45% project target for `libbubblehub` and `bubblehub`). See [CONTRIBUTING.md](CONTRIBUTING.md#coverage) for the local coverage command and HTML report locations.

Integration tests also need persistent caches for the model and OpenClaw dependencies. Use Docker named volumes instead of `$PWD` bind mounts, which can fail on remote/NFS workspaces:

```bash
docker volume create bubblehub-cache-local
docker volume create bubblehub-openclaw-local

docker build -f docker/Dockerfile --target integration-test -t bubblehub:integration .
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v bubblehub-cache-local:/cache/bubblehub \
  -v bubblehub-openclaw-local:/cache/openclaw \
  bubblehub:integration
```

### Interactive Docker Development

To explore the integration image interactively instead of running pytest:

```bash
docker run -it --rm \
  --privileged \
  --security-opt seccomp=unconfined \
  -e BUBBLEHUB_CACHE=/cache/bubblehub \
  -e BUBBLEHUB_MODELS_CONFIG=/cache/bubblehub/ci-models.yaml \
  -e BUBBLEHUB_INTEGRATION_WORKSPACE_DIR=/cache/bubblehub/integration-workspaces \
  -e OPENCLAW_CACHE_DIR=/cache/openclaw \
  -v bubblehub-cache-local:/cache/bubblehub \
  -v bubblehub-openclaw-local:/cache/openclaw \
  bubblehub:integration \
  bash
```

Inside the container:

```bash
scripts/ci/write-ci-model-config.sh
scripts/ci/prepare-openclaw.sh
mkdir -p /cache/bubblehub/integration-workspaces/dev-playground
bubblehub shell --allow-network --root-dir /cache/bubblehub/integration-workspaces/dev-playground
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines, testing requirements, and pull request expectations.