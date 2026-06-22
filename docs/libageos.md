# libageos Architecture

`libageos` is the native runtime for AgeOS scheduling, sandboxing, and local model hosting. It owns backend process lifecycle, warm-model cache state, admission decisions, scheduler state, and sandbox enforcement.

## Architecture

The native layer has three main surfaces:

- Scheduler state: records agents, queued model work, resource limits, warm model processes, ports, pids, and refcounts.
- Inference hosting: accepts JSON chat requests, admits model work, reuses or starts backend processes, forwards chat requests to those backends, and keeps loaded models warm after callers return.
- Sandbox execution: runs agent binaries with restricted filesystem, user, environment, resource, and network access. Installed sandboxes use an Ubuntu root filesystem plus per-agent overlayfs upper/work directories for private copy-up writes.

Call flow for model hosting:

1. A caller passes a JSON request containing specialty, selected model metadata, model path, messages, niceness, and token limits.
2. `ageos_inference_chat_json()` parses the request and checks the shared scheduler state.
3. If a healthy warm model exists, `libageos` increments the model refcount and reuses its pid/port.
4. If no healthy model exists, `libageos` admits the job and starts the configured native backend process.
5. `libageos` sends the OpenAI-shaped chat request to the backend and returns JSON to the caller.
6. The model refcount is released, but the model process stays warm until explicit eviction or scheduler pressure removes it.

Sandboxed agents receive an inference-only loopback path to the host AgeOS endpoint. Python code running inside the sandbox must use that forwarded endpoint instead of loading the native shared library directly.

## Hardening Policy

Hardening is enforced by the native sandbox runtime.

- Filesystem exposure must be explicit. A host `--root-dir` is mounted into the sandbox workspace view, not exposed as a host path in `$PWD`.
- The root filesystem lowerdir must stay read-only. Agent writes outside the workspace must land in the per-agent overlay upperdir.
- Sandbox `$PWD`, `$HOME`, `$TMPDIR`, `$AGEOS_WORKSPACE`, agent identity, `PATH`, locale, terminal type, and shell prompt must be set by the native sandbox setup.
- Persistent agent homes must live under controlled `.ageos/agents/agt-*` directories and must not follow symlinks.
- Agent homes should include standard shell profile files when first created so common installer scripts can persist user setup without host-specific exceptions.
- Network isolation is default for agents. Sandboxed agents only receive the local inference endpoint when `isolate_network` is enabled, and may receive general outbound network access only when the caller explicitly disables network isolation.
- Resource limits such as memory and CPU are applied by native sandbox setup.
- Native code should prefer fail-closed behavior. If setup, mount, user isolation, scheduler state, or inference forwarding cannot be established, return an error instead of silently relaxing restrictions.

## LLM Hosting Policy

`libageos` decides whether a model is loaded, reused, evicted, or started.

- Backend health validation, pid/port stability, refcount updates, and scheduler model records are native responsibilities.
- LLM hosting remains backend-agnostic at the API boundary: callers pass model metadata, while native code chooses the backend start path based on the model backend field.
- Chat is currently supported through the native JSON API.
- Embeddings should be added as a native API before being exposed as a supported runtime feature.
- Compatibility endpoints may speak OpenAI-shaped HTTP externally, while native inference keeps the shared model lifecycle and cache.

## File Responsibilities

### `libageos/meson.build`

Build definition for the native shared library and sandbox executable. Add new native source files here when responsibilities are split out of large C modules.

### `libageos/hw_detect.c`

Detects host RAM, VRAM, free VRAM, and GPU characteristics for scheduler and model selection. This informs admission policy but does not start models.

### `libageos/include/ageos/hw.h`

Public C declarations for hardware detection functions exported by the shared library.

### `libageos/limits.c`

Native helpers for resource limit handling. Keep low-level limit parsing or enforcement helpers here when they are shared outside the sandbox implementation.

### `libageos/include/ageos/limits.h`

Public declarations for native limit helpers.

### `libageos/landlock.c`

Linux Landlock setup and filesystem access restriction helpers. This file is part of the sandbox hardening boundary and should fail closed if rules cannot be applied safely.

### `libageos/sandbox.c`

Native sandbox runtime. It sets up the agent execution environment, persistent home/workspace mapping, user identity, clean environment variables, writable paths, network policy, resource limits, and the final exec.

Hardening policy for this file:

- Do not expose host root or host working directories as sandbox `$PWD`.
- Keep `/workspace` as the sandbox-facing root-dir mount point.
- Reject unsafe persistent paths and symlink escapes.
- Keep inference-only networking separate from explicit general outbound networking.

### `libageos/overfs.c`

Overlay and mount setup for sandbox filesystems. It mounts the Ubuntu rootfs lowerdir with the per-agent overlay upper/work directories, binds the workspace and AgeOS runtime paths, prepares `/dev`, `/proc`, `/tmp`, and other filesystem views, and logs mount decisions for debugging.

### `libageos/include/ageos/sandbox.h`

Public sandbox configuration struct and `ageos_sandbox_run()` declaration. Native code interprets the requested policy and enforces the sandbox.

### `libageos/scheduler.c`

Shared scheduler state and native inference core. It manages state file locking, agents, resource limits, queue entries, model records, model admission, model eviction, warm-model lookup, backend process spawning, backend health checks, chat forwarding, and JSON snapshots.

LLM hosting policy for this file:

- Own all backend process lifecycle.
- Keep model records stable across entrypoints and processes through the shared scheduler state path.
- Increment model refcounts while a native chat call is active.
- Mark models idle after a call, but keep backend processes warm.
- Evict unhealthy backend records before starting a replacement.
- Start supported backends natively, including `llama-server` and vLLM.

### `libageos/include/ageos/scheduler.h`

Public scheduler and inference API. This is the native ABI for language bindings, command wrappers, and service processes. New inference operations should be added here as native JSON-in/JSON-out APIs.

### `libageos/ageos_sandbox_main.c`

Small executable entrypoint for running the native sandbox from a process boundary. Keep policy in `sandbox.c`; this file should stay a thin command wrapper.
