# Python Layer Architecture

The Python layer is the user-facing control plane for AgeOS. It owns CLI parsing, HTTP payload normalization, model registry/config loading, model file downloads, Python integration adapters, and `ctypes` marshalling into `libageos`.

Python does not own sandbox hardening or LLM hosting. It must not start model backend processes, maintain warm-model caches, or decide whether a loaded model is reused.

## Architecture

Python entrypoints converge on the native library on the host, while sandboxed Python inference calls use the sandbox-forwarded HTTP endpoint:

- CLI commands parse user options and call `SchedulerClient` or `EngineSession`.
- HTTP and SDK-compatible shims normalize external payloads and call `EngineSession`.
- `EngineSession` selects a configured model candidate and ensures files exist, then sends one JSON request through `SchedulerClient.inference_chat()`.
- When `AGEOS_SANDBOX=1`, `EngineSession` must not initialize `SchedulerClient` or load `libageos`; it forwards chat requests to `AGEOS_SANDBOX_INFERENCE_HOST:AGEOS_SANDBOX_INFERENCE_PORT`.
- `SchedulerClient` delegates to `ageos/native.py`.
- `ageos/native.py` calls exported `libageos` functions with `ctypes`.

The Python layer is allowed to prepare data for native calls. It is not allowed to become a second scheduler, cache, sandbox, or model host.

## Hardening Policy

Python should express policy and validate user input before calling native code, but security enforcement belongs to `libageos`.

- CLI code may reject unsafe obvious input, such as protected `--root-dir` locations.
- Persistent sandbox metadata may be selected in Python, but path safety must be conservative: no symlink agent homes, no path escapes, and no source-tree roots unless explicitly allowed.
- Python must pass sandbox configuration to `ageos_sandbox_run()` instead of emulating isolation.
- `--unsafe-no-sandbox` is only a development escape hatch and must not be used as a production hardening path.
- Sandboxed inference environment variables are the only supported route for Python prompt/shim inference inside the sandbox; the native sandbox still controls network access.
- Python code should never weaken native failures. If native sandbox or inference setup fails, surface the error.

## LLM Hosting Policy

All model hosting is native-owned.

- Python must not call `subprocess.Popen()` to start `llama-server`, vLLM, or any model-serving process.
- Python must not call backend `/v1/chat/completions` endpoints directly for AgeOS inference. The exception is sandboxed `EngineSession`, which may call the sandbox-forwarded AgeOS compatibility endpoint because direct shared-library access would escape the sandbox boundary.
- Python must not keep a process, port, pid, refcount, or model-session cache.
- Python may select a model candidate from config and ensure model files are present.
- Python may marshal messages, max token limits, niceness, model metadata, and model paths into the native JSON request.
- Embeddings should not have Python fallback vectors. If native embeddings are not implemented, Python should return the native error.

## File Responsibilities

### `ageos/__init__.py`

Package metadata and top-level version surface.

### `ageos/native.py`

`ctypes` binding to `libageos`. It loads `libageos.so`, configures exported function signatures, converts Python values to C types, decodes native JSON strings, raises `LibAgeosError` for native failures, and exposes native hardware, scheduler, inference, and sandbox functions.

Policy:

- Keep this file as a binding layer, not a policy engine.
- Add new native inference surfaces here only after they exist in `libageos`.

### `ageos/inference.py`

Configuration and lifecycle helper for the local OpenAI-compatible HTTP daemon. It resolves host/port/default specialty, checks daemon health, starts `ageos.cli.inference_daemon` when needed, and prepares compatibility environment variables for agents.

Policy:

- This file may start the Python HTTP compatibility daemon.
- It must not start model backends. The daemon itself routes model work into native inference.

### `ageos/gpu_setup.py`

Installer/setup helper for selecting GPU runtime profile and optional vLLM dependencies. This influences installed capabilities, not runtime model hosting ownership.

### `ageos/config/__init__.py`

Package marker for bundled YAML configuration.

### `ageos/config/models.yaml`

Bundled model registry, specialties, scheduler limits, and inference defaults. Runtime selection reads this config, but native code owns actual model process state.

## CLI Files

### `ageos/cli/main.py`

Typer application root. Registers top-level commands and model/specialty subcommands. It should stay as command wiring and light presentation.

### `ageos/cli/run.py`

Implements `ageos run`. It resolves binaries, maps host root/workdir into sandbox paths, stages non-system binaries into a temporary workspace when no `--root-dir` is provided, resolves the installed Ubuntu rootfs and per-agent overlay paths, handles persistent sandbox reuse metadata, registers/deregisters agents, prepares compatibility inference env vars, and calls native sandbox execution.

Hardening policy:

- Keep protected-root validation conservative.
- Keep `/workspace` as the sandbox-facing root.
- Require non-system `--binary` paths to be inside `--root-dir` when a root is provided; allow system binaries so `ageos shell --root-dir <dir>` can enter an existing workspace.
- Pass rootfs and overlay paths to native code; do not mount or emulate overlay behavior in Python.
- Do not bypass native sandboxing except through explicit `--unsafe-no-sandbox`.

### `ageos/cli/shell.py`

Implements `ageos shell` by delegating to `run_agent()` with a shell binary. It should inherit sandbox behavior from `run.py`.

### `ageos/cli/prompt.py`

One-shot local prompt command. It handles text/structured-output workflow and calls `EngineSession` for native-backed chat.

### `ageos/cli/poc.py`

Interactive local model REPL. It opens one `EngineSession` and sends user messages through native-backed chat.

### `ageos/cli/serve.py`

Starts the HTTP compatibility server from resolved inference config. It does not host models directly.

### `ageos/cli/inference_daemon.py`

Daemon entrypoint for the HTTP compatibility server. It loads inference config and runs `http_api.run_http_api()`.

### `ageos/cli/ps.py`

Displays scheduler agent/model status from the native scheduler snapshot.

### `ageos/cli/queue.py`

Displays queued scheduler work from native scheduler state.

### `ageos/cli/dashboard.py`

Launches or renders the dashboard command surface. It should consume scheduler state rather than duplicate it.

### `ageos/cli/__init__.py`

Package marker for CLI modules.

## HTTP and Integrations

### `ageos/http_api.py`

OpenAI-compatible and Responses-compatible HTTP surface. It validates and normalizes request bodies, translates streaming/non-streaming response shapes, resolves specialty aliases, and calls `EngineSession`.

Policy:

- Do not keep a Python session cache.
- Do not generate local embedding fallback vectors.
- Do not call model backend HTTP endpoints directly.

### `ageos/integrations/openai_shim.py`

Tiny OpenAI-style Python client surface for `chat.completions.create`. It normalizes max-token arguments and calls `EngineSession`.

### `ageos/integrations/anthropic_shim.py`

Anthropic-style messages client. It converts Anthropic content blocks/system prompts into AgeOS chat messages and calls `EngineSession`.

### `ageos/integrations/langchain.py`

LangChain chat model adapter. It converts LangChain message classes into role/content dictionaries and calls `EngineSession`.

### `ageos/integrations/__init__.py`

Package marker for Python integration adapters.

## Engine Files

### `ageos/engine/session.py`

Thin model session facade. On the host, it loads model config, detects hardware, applies scheduler resource limits, selects the first valid model candidate, ensures model files exist, and marshals chat requests into `SchedulerClient.inference_chat()`. Inside the sandbox, it skips native scheduler setup and forwards chat requests to the sandbox inference endpoint.

Policy:

- No backend process objects.
- No warm model cache.
- No pid/port/refcount lifecycle ownership.
- No native scheduler/shared-library initialization when `AGEOS_SANDBOX=1`.
- No Python embeddings fallback.

### `ageos/engine/registry.py`

Loads and resolves model registry YAML. It supports bundled config, user override config, explicit `AGEOS_MODELS_CONFIG`, specialty resolution, model filtering, and ordering by placement/tier.

### `ageos/engine/downloader.py`

Ensures selected model files exist in the AgeOS cache using `huggingface_hub` when needed. It may download files but must not start or attach to backends.

### `ageos/engine/selector.py`

Chooses model tier ordering based on detected hardware capabilities.

### `ageos/engine/structured.py`

Helpers for structured JSON prompting, repair prompts, schema example loading, and JSON parsing.

### `ageos/engine/__init__.py`

Package marker and high-level engine package description.

## Node and Scheduler Client Files

### `ageos/node/client.py`

Python facade over `LibAgeos`. It provides a more ergonomic scheduler client for registering agents, checking limits, updating model records, reading snapshots, evicting models, and calling native inference.

Policy:

- Delegate model lifecycle decisions to `libageos`.
- Keep IDs and Python call shapes convenient, but do not cache native state.

### `ageos/node/daemon.py`

Legacy or service-style node daemon entrypoint. It should interact with native scheduler state rather than maintaining separate scheduler state.

### `ageos/node/telemetry.py`

Telemetry helpers for node/runtime reporting. This should observe state, not own scheduling or hardening decisions.

### `ageos/node/__init__.py`

Package marker for node-facing modules.

## TUI Files

### `ageos/tui/dashboard.py`

Terminal dashboard presentation for runtime state. It should read scheduler/client state and render it without mutating model hosting policy.

### `ageos/tui/__init__.py`

Package marker for TUI modules.

## Development Rules

- New user-facing inference entrypoints must call `EngineSession` or `SchedulerClient.inference_chat()`.
- New native capabilities must be added to `libageos` first, then exposed in `ageos/native.py`.
- Tests for model caching should assert stable native pid/port and single backend start across Python entrypoints.
- Tests for sandbox behavior should assert native workspace/home/PWD behavior and no host path leak.
- Any code that imports backend process adapters or starts model processes from Python violates the architecture.
