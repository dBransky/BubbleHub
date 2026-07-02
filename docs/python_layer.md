# Python Layer Architecture

The Python layer is the user-facing control plane for BubbleHub. It owns CLI parsing, HTTP payload normalization, model registry/config loading, model file downloads, Python integration adapters, and `ctypes` marshalling into `libbubblehub`.

Python does not own sandbox hardening or LLM hosting. It must not start model backend processes, maintain warm-model caches, or decide whether a loaded model is reused.

## Architecture

Python entrypoints converge on the native library on the host, while sandboxed Python inference calls use the sandbox-forwarded HTTP endpoint:

- CLI commands parse user options and call `SchedulerClient` or `EngineSession`.
- HTTP and SDK-compatible shims normalize external payloads and call `EngineSession`.
- `EngineSession` selects a configured model candidate and ensures files exist, then sends one JSON request through `SchedulerClient.inference_chat()`.
- When `BUBBLEHUB_SANDBOX=1`, `EngineSession` must not initialize `SchedulerClient` or load `libbubblehub`; it forwards chat requests to `BUBBLEHUB_SANDBOX_INFERENCE_HOST:BUBBLEHUB_SANDBOX_INFERENCE_PORT`.
- `SchedulerClient` delegates to `bubblehub/native.py`.
- `bubblehub/native.py` calls exported `libbubblehub` functions with `ctypes`.

The Python layer is allowed to prepare data for native calls. It is not allowed to become a second scheduler, cache, sandbox, or model host.

## Hardening Policy

Python should express policy and validate user input before calling native code, but security enforcement belongs to `libbubblehub`.

- CLI code may reject unsafe obvious input, such as protected `--root-dir` locations.
- Persistent sandbox metadata may be selected in Python, but path safety must be conservative: no symlink agent homes, no path escapes, and no source-tree roots unless explicitly allowed.
- Persistent sandbox access manifests are native-owned. Python may display pending access requests and submit a selected policy through `bubblehub/native.py`, but it must not parse, validate, or write manifest JSON directly.
- Python must pass sandbox configuration to `bubblehub_sandbox_run()` instead of emulating isolation.
- `--unsafe-no-sandbox` is only a development escape hatch and must not be used as a production hardening path.
- Sandboxed inference environment variables are the only supported route for Python prompt/shim inference inside the sandbox; the native sandbox still controls network access.
- Python code should never weaken native failures. If native sandbox or inference setup fails, surface the error.

## LLM Hosting Policy

All model hosting is native-owned.

- Python must not call `subprocess.Popen()` to start `llama-server`, vLLM, or any model-serving process.
- Python must not call backend `/v1/chat/completions` endpoints directly for BubbleHub inference. The exception is sandboxed `EngineSession`, which may call the sandbox-forwarded BubbleHub compatibility endpoint because direct shared-library access would escape the sandbox boundary.
- Python must not keep a process, port, pid, refcount, or model-session cache.
- Python may select a model candidate from config and ensure model files are present.
- Python may marshal messages, max token limits, niceness, model metadata, and model paths into the native JSON request.
- Embeddings should not have Python fallback vectors. If native embeddings are not implemented, Python should return the native error.

## File Responsibilities

### `bubblehub/__init__.py`

Package metadata and top-level version surface.

### `bubblehub/native.py`

`ctypes` binding to `libbubblehub`. It loads `libbubblehub.so`, configures exported function signatures, converts Python values to C types, decodes native JSON strings, raises `LibBubbleHubError` for native failures, and exposes native hardware, scheduler, inference, and sandbox functions.

Policy:

- Keep this file as a binding layer, not a policy engine.
- Add new native inference surfaces here only after they exist in `libbubblehub`.

### `bubblehub/inference.py`

Configuration and lifecycle helper for the local OpenAI-compatible HTTP daemon. It resolves host/port/default specialty, checks daemon health, starts `bubblehub.cli.inference_daemon` when needed, and prepares compatibility environment variables for agents.

Policy:

- This file may start the Python HTTP compatibility daemon.
- It must not start model backends. The daemon itself routes model work into native inference.

### `bubblehub/gpu_setup.py`

Installer/setup helper for selecting GPU runtime profile and optional vLLM dependencies. This influences installed capabilities, not runtime model hosting ownership.

### `bubblehub/config/__init__.py`

Package marker for bundled YAML configuration.

### `bubblehub/config/models.yaml`

Bundled model registry, specialties, scheduler limits, and inference defaults. Runtime selection reads this config, but native code owns actual model process state.

## CLI Files

### `bubblehub/cli/main.py`

Typer application root. Registers top-level commands and model/specialty subcommands. It should stay as command wiring and light presentation.

### `bubblehub/cli/run.py`

Implements `bubblehub run`. It resolves binaries, maps host root/workdir into sandbox paths, stages non-system binaries into a temporary workspace when no `--root-dir` is provided, resolves the installed Ubuntu rootfs and per-agent overlay paths, handles persistent sandbox reuse metadata, registers/deregisters agents, prepares compatibility inference env vars, and calls native sandbox execution.

Hardening policy:

- Keep protected-root validation conservative.
- Keep `/workspace` as the sandbox-facing root.
- Require non-system `--binary` paths to be inside `--root-dir` when a root is provided; allow system binaries so `bubblehub shell --root-dir <dir>` can enter an existing workspace.
- Pass rootfs and overlay paths to native code; do not mount or emulate overlay behavior in Python.
- Do not bypass native sandboxing except through explicit `--unsafe-no-sandbox`.

### `bubblehub/cli/shell.py`

Implements `bubblehub shell` by delegating to `run_agent()` with a shell binary. It should inherit sandbox behavior from `run.py`.

### `bubblehub/cli/prompt.py`

One-shot local prompt command. It handles text/structured-output workflow and calls `EngineSession` for native-backed chat.

### `bubblehub/cli/poc.py`

Interactive local model REPL. It opens one `EngineSession` and sends user messages through native-backed chat.

### `bubblehub/cli/serve.py`

Starts the HTTP compatibility server from resolved inference config. It does not host models directly.

### `bubblehub/cli/inference_daemon.py`

Daemon entrypoint for the HTTP compatibility server. It loads inference config and runs `http_api.run_http_api()`.

### `bubblehub/cli/ps.py`

Displays scheduler agent/model status from the native scheduler snapshot.

### `bubblehub/cli/queue.py`

Displays queued scheduler work from native scheduler state.

### `bubblehub/cli/dashboard.py`

Launches or renders the dashboard command surface. It should consume scheduler state rather than duplicate it.

### `bubblehub/cli/__init__.py`

Package marker for CLI modules.

## HTTP and Integrations

### `bubblehub/http_api.py`

OpenAI-compatible and Responses-compatible HTTP surface. It validates and normalizes request bodies, translates streaming/non-streaming response shapes, resolves specialty aliases, and calls `EngineSession`.

Policy:

- Do not keep a Python session cache.
- Do not generate local embedding fallback vectors.
- Do not call model backend HTTP endpoints directly.

### `bubblehub/integrations/openai_shim.py`

Tiny OpenAI-style Python client surface for `chat.completions.create`. It normalizes max-token arguments and calls `EngineSession`.

### `bubblehub/integrations/anthropic_shim.py`

Anthropic-style messages client. It converts Anthropic content blocks/system prompts into BubbleHub chat messages and calls `EngineSession`.

### `bubblehub/integrations/langchain.py`

LangChain chat model adapter. It converts LangChain message classes into role/content dictionaries and calls `EngineSession`.

### `bubblehub/integrations/__init__.py`

Package marker for Python integration adapters.

## Engine Files

### `bubblehub/engine/session.py`

Thin model session facade. On the host, it loads model config, detects hardware, applies scheduler resource limits, selects the first valid model candidate, ensures model files exist, and marshals chat requests into `SchedulerClient.inference_chat()`. Inside the sandbox, it skips native scheduler setup and forwards chat requests to the sandbox inference endpoint.

Policy:

- No backend process objects.
- No warm model cache.
- No pid/port/refcount lifecycle ownership.
- No native scheduler/shared-library initialization when `BUBBLEHUB_SANDBOX=1`.
- No Python embeddings fallback.

### `bubblehub/engine/registry.py`

Loads and resolves model registry YAML. It supports bundled config, user override config, explicit `BUBBLEHUB_MODELS_CONFIG`, specialty resolution, model filtering, and ordering by placement/tier.

### `bubblehub/engine/downloader.py`

Ensures selected model files exist in the BubbleHub cache using `huggingface_hub` when needed. It may download files but must not start or attach to backends.

### `bubblehub/engine/selector.py`

Chooses model tier ordering based on detected hardware capabilities.

### `bubblehub/engine/structured.py`

Helpers for structured JSON prompting, repair prompts, schema example loading, and JSON parsing.

### `bubblehub/engine/__init__.py`

Package marker and high-level engine package description.

## Node and Scheduler Client Files

### `bubblehub/node/client.py`

Python facade over `LibBubbleHub`. It provides a more ergonomic scheduler client for registering agents, checking limits, updating model records, reading snapshots, evicting models, and calling native inference.

Policy:

- Delegate model lifecycle decisions to `libbubblehub`.
- Keep IDs and Python call shapes convenient, but do not cache native state.

### `bubblehub/node/daemon.py`

Legacy or service-style node daemon entrypoint. It should interact with native scheduler state rather than maintaining separate scheduler state.

### `bubblehub/node/telemetry.py`

Telemetry helpers for node/runtime reporting. This should observe state, not own scheduling or hardening decisions.

### `bubblehub/node/__init__.py`

Package marker for node-facing modules.

## TUI Files

### `bubblehub/tui/dashboard.py`

Terminal dashboard presentation for runtime state. It should read scheduler/client state and render it without mutating model hosting policy.

### `bubblehub/tui/__init__.py`

Package marker for TUI modules.

## Development Rules

- New user-facing inference entrypoints must call `EngineSession` or `SchedulerClient.inference_chat()`.
- New native capabilities must be added to `libbubblehub` first, then exposed in `bubblehub/native.py`.
- Tests for model caching should assert stable native pid/port and single backend start across Python entrypoints.
- Tests for sandbox behavior should assert native workspace/home/PWD behavior and no host path leak.
- Any code that imports backend process adapters or starts model processes from Python violates the architecture.
