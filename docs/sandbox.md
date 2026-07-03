# Sandbox Architecture

The BubbleHub sandbox is a Linux-only native sandbox for running agent binaries with restricted filesystem, user, resource, and network access. It is implemented in `libbubble`, with Python acting as the control plane that resolves CLI options and calls the native sandbox helper.

This document is intentionally engineering-facing. It describes the exact execution flow and is explicit about what the sandbox does not yet protect.

## Security Goal

The sandbox is designed to make normal agent execution run as a non-root, namespaced process with a constrained view of the filesystem and, by default, no general outbound network access.

It is not a complete VM boundary. Treat it as a host-process sandbox built from Linux primitives: user namespaces, mount namespaces, optional network namespaces, Landlock, cgroups/resource limits, and optional seccomp. Its safety depends on kernel behavior, install permissions, correct helper setup, and the absence of kernel or setuid-helper bugs.

## Entry Points

The normal CLI flow is:

1. `bubble run` or `bubble shell` enters Python CLI code in `bubblehub/cli/run.py` or `bubblehub/cli/shell.py`.
2. Python resolves the requested binary, workspace, optional rootfs, persistent agent id, overlay paths, and inference environment.
3. Python calls `NativeScheduler.run_sandbox()` in `bubblehub/native.py`.
4. `NativeScheduler.run_sandbox()` executes the installed native helper, normally `bubblehub-sandbox`.
5. `bubblehub-sandbox` parses flags in `libbubble/bubblehub_sandbox_main.c` and calls `bubblehub_sandbox_run()` in `libbubble/sandbox.c`.

The important detail is step 4: sandbox setup runs through the native helper process, not inside the Python process through `ctypes`. On hosts with `kernel.apparmor_restrict_unprivileged_userns=1`, an ordinary Python process can create a user namespace but may not receive the capabilities needed to finish mount and network namespace setup. The installed helper is therefore root-owned setuid and is expected to be installed by `scripts/build.sh`.

## Native Helper Requirements

`scripts/build.sh` installs `/usr/local/bin/bubblehub-sandbox` as:

```text
owner: root:root
mode: 4755
```

That is not decorative. It is required on hardened Ubuntu/AppArmor hosts where unprivileged user namespaces are restricted. Without the helper privileges, sandbox setup can fail at UID/GID mapping or at later namespace/mount setup with `EPERM`.

The helper should stay small. Policy belongs in `libbubble/sandbox.c`; `bubblehub_sandbox_main.c` should remain a thin argument parser and native entrypoint.

## Execution Flow

Inside `bubblehub_sandbox_run()` the flow is:

1. Initialize logging and validate the sandbox config.
2. Resolve host UID/GID using the real invoking user, then derive a sandbox agent UID/GID in the `60000-63999` range.
3. Create a temporary sandbox root under `/tmp/bubblehub-root-*`.
4. Chown that temporary root back to the invoking host UID/GID. This matters because the helper may be setuid root, but the eventual mapped sandbox identity must be able to prepare private runtime files inside the temporary root.
5. If inference forwarding is enabled, create a host-side control socket and fork a host inference proxy process.
6. Create synchronization pipes for user namespace setup.
7. Fork the sandbox child.
8. The child calls `unshare(CLONE_NEWUSER)` and signals the parent.
9. The parent writes the child's UID/GID maps through `/proc/<child>/uid_map`, `/proc/<child>/setgroups`, and `/proc/<child>/gid_map`.
10. The child waits until mapping is complete, switches to the mapped sandbox UID/GID, and continues setup.
11. The child creates the remaining namespaces: mount, IPC, UTS, and, unless network is explicitly allowed, network.
12. If network is isolated, bring up loopback and start the namespace-side inference proxy when configured.
13. Set up the filesystem view: rootfs/overlay when configured, workspace, home, tmp, identity files, and runtime binds.
14. Apply Landlock filesystem policy.
15. Enter the sandbox root/workdir.
16. Apply `no_new_privs`.
17. Optionally apply seccomp when `BUBBLEHUB_ENABLE_SECCOMP=1`.
18. Close extra file descriptors and `execv()` the agent binary.
19. The parent waits for the sandbox child and cleans up temporary state and helper processes.

## User Identity

The sandbox does not run the final agent as host root. BubbleHub maps one sandbox UID/GID to the real invoking host UID/GID and switches the child to a deterministic per-agent identity.

This gives the process a non-root identity inside the sandbox while still allowing writes to the host workspace paths that correspond to the invoking user. It also means same-user host metadata remains a sensitive area; see "Known Gaps" below.

## Filesystem Model

The configured `--root-dir` is the writable workspace boundary. Inside the sandbox, it is exposed through the agent workspace path, not as the raw host path.

The sandbox prepares:

- `HOME` under `/home/<agent>`.
- `BUBBLEHUB_WORKSPACE` under the agent home.
- `TMPDIR` under the agent home.
- `/etc/passwd` and `/etc/group` identity files for the sandbox user.
- Optional rootfs/overlay paths when an installed BubbleHub rootfs is configured.
- A private `/run` tmpfs in the sandbox mount namespace so host Unix control sockets, such as Docker or containerd sockets, are not reachable by pathname.

Landlock is applied after setup. It allows read access to required runtime paths such as `/usr`, `/bin`, libraries, certificates, and `/opt/bubblehub`; allows writes to the sandbox home/workspace and selected state paths; and denies ordinary access outside the configured policy.

## Network Model

Network isolation is the default for agents.

When `isolate_network=1`:

- A new network namespace is created.
- Loopback is brought up.
- The sandbox may receive a loopback inference endpoint that forwards to the host BubbleHub inference service.
- General outbound network access should fail because there is no external interface in the namespace.
- HTTP clients are pointed at a loopback policy proxy on `127.0.0.1:18080`. The listener runs in the sandbox network namespace, but accepted client sockets are passed to a host-side `libbubble` proxy process. That host process evaluates the persistent access manifest before either returning `403` or forwarding the request upstream.

## Access Manifests

Per-sandbox network policy is stored by `libbubble`, not Python. The default manifest path is:

```text
~/.local/state/bubblehub/sandboxes/<agent-id>/access-manifest.json
```

Tests and packaged deployments can override the state root with `BUBBLEHUB_STATE_DIR`. The manifest contains schema version, agent id, policy entries, and pending requests. Policy entries use `kind`, `subject`, `method`, `path`, and `policy` fields. For HTTP, `subject` is the normalized domain, `method` is the visible HTTP verb, and `path` is the visible request path. HTTPS `CONNECT` requests only expose host/port and the `CONNECT` method.

Valid policies are:

- `always`: forward matching requests through the host proxy.
- `never`: return `403` for matching requests.
- `ask`: prompt again when an interactive host is available. In the interactive shell prompt this approves the current request, then asks again next time. If no prompt is possible, BubbleHub records pending and denies the current request.

Unknown, missing, malformed, or timed-out policy decisions are fail-closed. If `bubble run` or `bubble shell` is attached to a real terminal, the host CLI pauses the sandboxed agent and asks for a policy decision. If no interactive prompt is possible, the native proxy records the request as pending and returns `403` with a dashboard hint rather than allowing traffic or hanging the agent. `bubble dashboard` lists pending requests through exported `libbubble` APIs and submits host decisions back to native code; for HTTP requests, dashboard decisions are host-scoped so an approved HTTP request also covers a later HTTPS `CONNECT` to the same host. `bubble manifest --root-dir <dir>` and `bubble manifest --agent-id <agent>` inspect and edit persisted policies. Python does not parse or edit manifest JSON directly.

When `allow_network` is requested:

- BubbleHub does not create a new network namespace.
- The agent shares host network reachability.
- Filesystem and identity sandboxing still apply, but network isolation does not.

Be careful when reading test names: CLI `--allow-network` means `isolate_network=False`.

## Resource Controls

`bubblehub_apply_cgroup_limits()` applies configured resource limits before final execution. Memory, CPU percent, and niceness are passed through the native helper path.

Resource controls are not a confidentiality boundary. They are operational controls to reduce host pressure and scheduler abuse.

## Seccomp

Seccomp is optional and controlled by `BUBBLEHUB_ENABLE_SECCOMP=1`.

When enabled, BubbleHub loads an allowlist-style syscall policy. The allowlist differs depending on whether general networking is allowed. When disabled, sandboxing still relies on namespaces, Landlock, user identity, and mount setup, but syscall filtering is not active.

Do not claim seccomp protection unless the environment explicitly enables it and tests cover that mode.

## What We Test

The sandbox test coverage includes:

- CLI end-to-end shell execution in `tests/test_bash_cli.py`.
- Native sandbox execution, identity, persistent home, rootfs overlay, protected path denial, and environment behavior in `tests/test_sandbox.py`.
- Network allowed/isolated behavior in `tests/test_sandbox_network.py`.
- Escape attempts in `tests/test_sandbox_escape.py`.

`tests/test_sandbox_escape.py` runs separate Python-level and C-level escape fixtures as category-level pytest cases in both network modes. The Python fixture covers high-level filesystem and process attempts. The C fixture uses direct syscalls for host canary access, symlink/hardlink/rename tricks, `/proc/*/root` aliases, writes to protected runtime paths, proc sysctl writes, host Unix socket connects, namespace entry, mount and pivot-root attempts, ptrace, BPF/perf/module/kexec/reboot/swap/I/O privilege syscalls, and public network connect when network isolation is enabled.

These tests are useful regression coverage. They are not a proof of sandbox soundness.

## Safety Assessment

Current safety level: useful development sandbox, not a high-assurance hostile-code containment boundary.

The sandbox gives meaningful protection against accidental or cooperative agent access outside the workspace, common filesystem writes, host namespace entry, and default outbound network access. It is also fail-closed in important setup paths: if user namespaces, mount setup, Landlock, or runtime env setup fail, the sandbox returns an error rather than silently running unsandboxed.

However, the sandbox should not yet be treated like a VM, microVM, or browser-grade renderer sandbox. Running actively malicious code still carries real host risk.

## Known Gaps

- The native helper is setuid root. That is required on the current hardened Ubuntu/AppArmor host, but it makes `bubblehub-sandbox` a privileged attack surface. Keep it small, audited, and free of complex parsing or policy decisions.
- Landlock does not currently block every metadata operation. In local testing, `chmod` on a same-user host-owned file outside the workspace was not blocked even though content read/write/link/rename attempts were blocked. This is a real gap, not a documentation artifact.
- Seccomp is opt-in through `BUBBLEHUB_ENABLE_SECCOMP=1`, not always-on.
- `allow_network` means the agent has host network reachability. It should only be used when that is truly intended.
- The sandbox relies on Linux kernel namespace and Landlock correctness. Kernel vulnerabilities or unexpected LSM interactions can break assumptions.
- The current model is not designed to resist a malicious agent with a fresh local privilege escalation exploit.
- The sandbox permits necessary reads of runtime/system paths. That is not the same as an empty filesystem view.
- The escape tests cover representative attacks, not an exhaustive adversarial corpus.

## Engineering Rules

- Do not add Python fallbacks that run an agent outside the native sandbox when sandbox setup fails.
- Keep filesystem and namespace enforcement in `libbubble`, not Python.
- Keep access manifest storage, matching, and pending request updates in `libbubble`; Python may render prompts or dashboard UI, but must apply decisions through native APIs.
- Keep `bubblehub-sandbox` as a thin helper and make `sandbox.c` own policy.
- Treat every new host path bind or Landlock allow rule as a security-sensitive change.
- If a setup step cannot be completed, return an error. Do not degrade silently.
- Add regression tests for every sandbox escape bug, including expected failures and host-side canary checks.
