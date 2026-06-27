<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ageos-logo-dark.png">
    <img src="assets/ageos-logo.png" alt="AgeOS logo" width="175">
  </picture>
  <p>Local LLM serving and sandboxed agents in one command.</p>
  <p>
    <a href="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ageos-labs/ageos-runtime/actions/workflows/ci.yml/badge.svg"></a>
    <a href="https://codecov.io/gh/ageos-labs/ageos-runtime"><img alt="Coverage" src="https://codecov.io/gh/ageos-labs/ageos-runtime/graph/badge.svg"></a>
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

The installer downloads the latest GitHub Release artifact, installs local runtime dependencies, builds AgeOS, and creates the Ubuntu 26.04 sandbox filesystem.
When you install from the CLI script, AgeOS asks whether to install the Tauri desktop app as well or keep the install CLI-only. You can skip
it and install it later with `ageos app`.
On Windows it checks for WSL2 and an installed Linux distro, installs AgeOS inside WSL, then creates Start Menu and Desktop shortcuts for
**AgeOS Control Center** when the desktop app is selected. The shortcuts launch the Tauri desktop app through WSL/WSLg. If no distro exists,
run `wsl --install -d Ubuntu`, complete Ubuntu setup, and rerun the installer.
Set `AGEOS_INSTALL_WSL=1` before running the installer to let it attempt the elevated WSL install step for you.

Check it:

```bash
ageos --help
```

Open the graphical Control Center:

```bash
ageos app
```

`ageos app` starts the AgeOS Control Center as a Tauri desktop app backed by a host-only localhost API. It shows RAM/VRAM usage,
loaded models with model-family logos, scheduler queue, running agents, sandbox access manifests, and base-model selection. The Agents tab can stop
an agent process or delete its persistent sandbox. Use `ageos app --server-only` only for diagnostics/headless API checks.

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

Name an agent for `ageos ps`, the shell prompt, and the Control Center:

```bash
ageos shell --name reviewer --root-dir ./workspace
ageos ps --kill agt-...
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

Open the Task Manager-style GUI:

```bash
ageos app
```

## What AgeOS Does

- Runs local LLMs.
- Exposes an OpenAI-compatible endpoint at `http://127.0.0.1:8000/v1`.
- Keeps warm model backends shared across agents.
- Runs agents in a Linux sandbox with restricted filesystem and network access.
- Injects local inference into agents as `OPENAI_BASE_URL` and `OPENAI_API_KEY`.
- Provides the AgeOS Control Center desktop app for graphical monitoring, manifest review, and base-model selection.

## Agent Environment

`ageos run` starts the shared inference endpoint before launching an agent and injects:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=ageos-local
AGEOS_API_BASE_URL=http://127.0.0.1:8000
AGEOS_SANDBOX_INFERENCE_HOST=127.0.0.1
AGEOS_SANDBOX_INFERENCE_PORT=8000
```

Sandboxed agents only get access to the local inference endpoint by default. HTTP clients see a loopback policy proxy at `http://127.0.0.1:18080`, and AgeOS injects `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, and `https_proxy` for isolated sandboxes. The proxy is owned by `libageos`: it checks the persistent sandbox access manifest under `~/.local/state/ageos/sandboxes/<agent-id>/access-manifest.json` (or `AGEOS_STATE_DIR`) and either forwards allowed requests or returns a logged `403`. Unknown requests fail closed; when no host prompt is available, AgeOS records them as pending in the manifest for later dashboard review. Use `--allow-network` with `ageos run` or `ageos shell` when an agent setup step needs general outbound network access.

When `ageos run` or `ageos shell` is connected to a real terminal, first access to a new host pauses the agent and prompts on the host for `always`, `never`, or `ask every time (approve now)`. Non-interactive runs fail closed and print a reminder to run `ageos dashboard`; the dashboard resolves pending sandbox access requests before the live resource view opens. Use `ageos manifest --root-dir <dir>` or `ageos manifest --agent-id <agent>` to inspect and edit persisted policies. Manifest policies are exactly `always`, `never`, or `ask`; HTTP policies match domains plus the visible HTTP method/path, while HTTPS `CONNECT` can only be matched at the host/port level.

When `--root-dir` is provided, non-system binaries must live inside that root and AgeOS mounts the root as the sandbox workspace. System binaries from `/usr`, `/bin`, `/sbin`, or `/opt/ageos` can still be used with a root directory, which lets `ageos shell --root-dir <dir>` open a shell inside the workspace sandbox. When `--root-dir` is omitted, non-system binaries are copied into a temporary workspace before the sandbox starts. Inside the sandbox, AgeOS Python prompt/shim calls detect `AGEOS_SANDBOX=1` and use the forwarded inference endpoint instead of loading the native shared library.

Installed sandboxes run over an Ubuntu 26.04 root filesystem using a per-agent overlay. The Ubuntu lower filesystem stays unchanged; writes outside the workspace copy up into `.ageos/agents/<agent-id>/overlay/upper` and persist with that agent. Use `--force-new-sandbox` or `--overwrite-sandbox` to discard the persistent home and private overlay for the current workspace.

For implementation details, security assumptions, and known gaps, see [`docs/sandbox.md`](docs/sandbox.md).

## OpenClaw Example

OpenClaw can be installed entirely from inside the sandbox. The persistent agent home keeps `nvm`, npm global packages, and OpenClaw config across runs.

```bash
ageos shell --allow-network --root-dir openclaw
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

Install dependencies and build AgeOS. The build installs the native `ageos-sandbox` helper under `/usr/local/bin` with the permissions required for sandbox setup, creates the Ubuntu 26.04 rootfs on first install, and preserves it on later local rebuilds for a faster development loop.

```bash
./scripts/install-deps.sh
./scripts/build.sh
```

## Test Before Push

Run the same Docker test targets that CI uses before pushing:

```bash
docker build -f docker/Dockerfile --target unit-test -t ageos-runtime:unit .
docker run --rm --privileged --security-opt seccomp=unconfined ageos-runtime:unit
```

CI enforces line coverage through [Codecov](https://codecov.io/gh/ageos-labs/ageos-runtime) (45% project target for `libageos` and `ageos`). See [CONTRIBUTING.md](CONTRIBUTING.md#coverage) for the local coverage command and HTML report locations.

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

### Interactive Docker Development

To explore the integration image interactively instead of running pytest:

```bash
docker run -it --rm \
  --privileged \
  --security-opt seccomp=unconfined \
  -e AGEOS_CACHE=/cache/ageos \
  -e AGEOS_MODELS_CONFIG=/cache/ageos/ci-models.yaml \
  -e AGEOS_INTEGRATION_WORKSPACE_DIR=/cache/ageos/integration-workspaces \
  -e OPENCLAW_CACHE_DIR=/cache/openclaw \
  -v ageos-cache-local:/cache/ageos \
  -v ageos-openclaw-local:/cache/openclaw \
  ageos-runtime:integration \
  bash
```

Inside the container:

```bash
scripts/ci/write-ci-model-config.sh
scripts/ci/prepare-openclaw.sh
mkdir -p /cache/ageos/integration-workspaces/dev-playground
ageos shell --allow-network --root-dir /cache/ageos/integration-workspaces/dev-playground
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines, testing requirements, and pull request expectations.