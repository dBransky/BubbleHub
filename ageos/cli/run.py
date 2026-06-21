from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import typer

from ageos.inference import apply_inference_env
from ageos.log import log_debug, log_error, log_info
from ageos.node.client import SchedulerClient

_AGENT_ID_RE = re.compile(r"^agt-[A-Za-z0-9_-]+$")
_AGEOS_DIR = ".ageos"
_AGENTS_DIR = "agents"
_CURRENT_AGENT_FILE = "current-agent"
_OVERLAY_DIR = "overlay"
_OVERLAY_UPPER_DIR = "upper"
_OVERLAY_WORK_DIR = "work"
_DEFAULT_ROOTFS_DIR = Path("/opt/ageos/rootfs/ubuntu-26.04")
_ROOTFS_RELEASE = "ubuntu-26.04"
_SANDBOX_SYSTEM_PREFIXES = (
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/opt/ageos"),
)


def command(
    ctx: typer.Context,
    binary: str = typer.Option(..., "--binary", help="Agent binary path or command name."),
    niceness: int = typer.Option(0, "--niceness", min=-20, max=19, help="AgeOS GPU/memory priority."),
    memory: str = typer.Option("2G", "--memory", help="Sandbox memory limit."),
    cpu: int = typer.Option(0, "--cpu", help="Optional cgroup CPU percent cap."),
    speciality: str | None = typer.Option(None, "--speciality", help="Default model specialty for this agent."),
    workdir: Path | None = typer.Option(None, "--workdir", file_okay=False, dir_okay=True),
    root_dir: Path | None = typer.Option(
        None,
        "--root-dir",
        file_okay=False,
        dir_okay=True,
        help="Writable directory exposed inside the sandbox. Defaults to an empty /workspace.",
    ),
    force_new_sandbox: bool = typer.Option(
        False,
        "--force-new-sandbox",
        "--overwrite-sandbox",
        help="Discard any persistent sandbox under --root-dir and start with a new agent home.",
    ),
    unsafe_no_sandbox: bool = typer.Option(False, "--unsafe-no-sandbox", help="Development escape hatch only."),
    allow_network: bool = typer.Option(False, "--allow-network", help="Allow network access for the agent. This is useful for setting up the agent inside the sandbox environment."),
) -> None:
    """Run a binary as an AgeOS agent inside the hardened sandbox."""

    run_agent(
        binary=binary,
        extra_args=list(ctx.args),
        niceness=niceness,
        memory=memory,
        cpu=cpu,
        speciality=speciality,
        workdir=workdir,
        root_dir=root_dir,
        force_new_sandbox=force_new_sandbox,
        unsafe_no_sandbox=unsafe_no_sandbox,
        allow_network=allow_network,
    )


def run_agent(
    *,
    binary: str,
    extra_args: list[str],
    niceness: int,
    memory: str,
    cpu: int,
    speciality: str | None,
    workdir: Path | None,
    root_dir: Path | None = None,
    unsafe_no_sandbox: bool = False,
    force_new_sandbox: bool = False,
    allow_network: bool = False,
) -> None:
    """Run a binary as an AgeOS agent inside the hardened sandbox."""

    client = SchedulerClient.local()
    sandbox_paths = _resolve_sandbox_paths(root_dir, workdir)
    rootfs_dir = _resolve_rootfs_dir()
    cwd_path = sandbox_paths.host_workdir
    resolved_binary = _resolve_binary(binary, cwd_path)
    persistent = _select_persistent_sandbox(sandbox_paths.host_root_dir, force_new=force_new_sandbox)
    agent_id = client.register_agent(
        str(resolved_binary),
        niceness=niceness,
        specialty=speciality,
        agent_id=persistent.agent_id,
    )
    _record_persistent_sandbox(sandbox_paths.host_root_dir, agent_id)
    if persistent.reused:
        log_info("reusing persistent sandbox", agent_id)
        typer.echo(f"Persistent sandbox found: reusing {agent_id}")
    sandbox_paths, sandbox_binary, staging_dir = _prepare_sandbox_binary(
        resolved_binary,
        sandbox_paths,
        agent_id,
    )
    overlay_workspace_dir: tempfile.TemporaryDirectory[str] | None = None
    if rootfs_dir is not None and sandbox_paths.host_root_dir is None:
        overlay_workspace_dir = tempfile.TemporaryDirectory(prefix="ageos-workspace-")
        host_root = Path(overlay_workspace_dir.name)
        sandbox_paths = SandboxPaths(
            host_workdir=host_root,
            sandbox_workdir="/workspace",
            host_root_dir=str(host_root),
        )
    overlay_paths = _prepare_overlay_paths(sandbox_paths.host_root_dir, agent_id, rootfs_dir)
    env = dict()
    env["AGEOS_AGENT_ID"] = agent_id
    env["AGEOS_NICENESS"] = str(niceness)
    # we want to see logs for the sandbox invocation, we will remove these later for the agent
    env["AGEOS_LOG_FILE"] = os.environ.get("AGEOS_LOG_FILE", "")
    env["AGEOS_LOG_LEVEL"] = os.environ.get("AGEOS_LOG_LEVEL", "")
    env["AGEOS_ENABLE_SECCOMP"] = os.environ.get("AGEOS_ENABLE_SECCOMP", "")
    ageos_pythonpath = _ageos_site_packages()
    if ageos_pythonpath is not None:
        env["AGEOS_PYTHONPATH"] = str(ageos_pythonpath)
    if rootfs_dir is not None:
        env["AGEOS_ROOTFS_RELEASE"] = _ROOTFS_RELEASE
    endpoint = apply_inference_env(env, speciality)
    log_info("using inference endpoint", endpoint)
    typer.echo(f"Using AgeOS inference endpoint at {endpoint}")
    cwd = sandbox_paths.sandbox_workdir
    sandbox_args = [*_argv_for_binary(sandbox_binary), *extra_args]
    host_args = [*_argv_for_binary(resolved_binary), *extra_args]
    log_debug(
        "launching agent",
        f"agent_id={agent_id} binary={resolved_binary} sandbox={not unsafe_no_sandbox} env={env}",
    )
    try:
        if platform.system() != "Linux" and not unsafe_no_sandbox:
            log_error("sandbox unavailable on platform", platform.system())
            raise typer.BadParameter("ageos run sandbox is Linux-only; use --unsafe-no-sandbox for local development")
        if unsafe_no_sandbox:
            log_info("running without sandbox", str(resolved_binary))
            raise typer.Exit(subprocess.call(host_args, cwd=sandbox_paths.host_workdir, env=env))
        inference = _sandbox_inference_endpoint(endpoint)
        _apply_sandbox_inference_env(env, inference)
        exit_code = _run_native_sandbox(
            client,
            sandbox_args,
            memory=memory,
            cpu=cpu,
            niceness=niceness,
            workdir=cwd,
            root_dir=sandbox_paths.host_root_dir,
            rootfs_dir=str(rootfs_dir) if rootfs_dir is not None else None,
            overlay_upper_dir=str(overlay_paths.upper_dir) if overlay_paths is not None else None,
            overlay_work_dir=str(overlay_paths.work_dir) if overlay_paths is not None else None,
            env=env,
            isolate_network=not allow_network,
            inference_host=inference.host,
            inference_port=inference.host_port,
            sandbox_inference_port=inference.sandbox_port,
        )
        log_debug("sandbox exited", f"agent_id={agent_id} exit_code={exit_code}")
        raise typer.Exit(exit_code)
    finally:
        if staging_dir is not None:
            staging_dir.cleanup()
        if overlay_workspace_dir is not None:
            overlay_workspace_dir.cleanup()
        client.deregister_agent(agent_id)


@dataclass(frozen=True)
class PersistentSandbox:
    agent_id: str | None
    reused: bool = False


@dataclass(frozen=True)
class OverlayPaths:
    upper_dir: Path
    work_dir: Path


def _select_persistent_sandbox(root_dir: str | None, *, force_new: bool) -> PersistentSandbox:
    if root_dir is None:
        return PersistentSandbox(agent_id=None)
    root = Path(root_dir)
    agent_id = _find_persistent_agent_id(root)
    if agent_id is None:
        return PersistentSandbox(agent_id=None)
    if force_new:
        _remove_persistent_agent(root, agent_id)
        return PersistentSandbox(agent_id=None)
    return PersistentSandbox(agent_id=agent_id, reused=True)


def _find_persistent_agent_id(root: Path) -> str | None:
    ageos_dir = root / _AGEOS_DIR
    agents_dir = ageos_dir / _AGENTS_DIR
    marker = ageos_dir / _CURRENT_AGENT_FILE
    if marker.is_file():
        agent_id = marker.read_text(encoding="utf-8").strip()
        if _is_persistent_agent_dir(agents_dir / agent_id):
            return agent_id

    if not agents_dir.is_dir():
        return None
    candidates = [path for path in agents_dir.iterdir() if _is_persistent_agent_dir(path)]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates[0].name


def _record_persistent_sandbox(root_dir: str | None, agent_id: str) -> None:
    if root_dir is None:
        return
    ageos_dir = Path(root_dir) / _AGEOS_DIR
    ageos_dir.mkdir(mode=0o700, exist_ok=True)
    (ageos_dir / _CURRENT_AGENT_FILE).write_text(f"{agent_id}\n", encoding="utf-8")


def _remove_persistent_agent(root: Path, agent_id: str) -> None:
    agents_dir = root / _AGEOS_DIR / _AGENTS_DIR
    agent_dir = agents_dir / agent_id
    if not _is_persistent_agent_dir(agent_dir):
        return
    resolved_agents = agents_dir.resolve()
    resolved_agent = agent_dir.resolve()
    if resolved_agent.parent != resolved_agents:
        raise typer.BadParameter("persistent sandbox path escaped .ageos/agents")
    shutil.rmtree(resolved_agent)


def _is_persistent_agent_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and not path.is_symlink()
        and _is_valid_agent_id(path.name)
        and (path / "home").is_dir()
        and not (path / "home").is_symlink()
    )


def _is_valid_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_RE.fullmatch(agent_id))


def _resolve_rootfs_dir() -> Path | None:
    if os.environ.get("AGEOS_DISABLE_ROOTFS") == "1":
        return None
    configured = os.environ.get("AGEOS_ROOTFS_DIR")
    rootfs = Path(configured).expanduser() if configured else _DEFAULT_ROOTFS_DIR
    if not rootfs.exists():
        if configured:
            raise typer.BadParameter(f"AgeOS rootfs not found: {rootfs}")
        return None
    if not rootfs.is_dir():
        raise typer.BadParameter(f"AgeOS rootfs is not a directory: {rootfs}")
    return rootfs.resolve()


def _prepare_overlay_paths(root_dir: str | None, agent_id: str, rootfs_dir: Path | None) -> OverlayPaths | None:
    if rootfs_dir is None or root_dir is None:
        return None
    if not _is_valid_agent_id(agent_id):
        raise typer.BadParameter(f"invalid agent id for persistent overlay: {agent_id}")
    agent_dir = Path(root_dir) / _AGEOS_DIR / _AGENTS_DIR / agent_id
    overlay_dir = agent_dir / _OVERLAY_DIR
    upper_dir = overlay_dir / _OVERLAY_UPPER_DIR
    work_dir = overlay_dir / _OVERLAY_WORK_DIR
    for path in (agent_dir, overlay_dir, upper_dir, work_dir):
        if path.is_symlink():
            raise typer.BadParameter(f"persistent sandbox path cannot be a symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return OverlayPaths(upper_dir=upper_dir.resolve(), work_dir=work_dir.resolve())


def _resolve_binary(binary: str, cwd: Path) -> Path:
    candidate = Path(binary).expanduser()
    if candidate.is_absolute() or candidate.parent != Path(".") or binary.startswith(("./", "../")):
        paths = [candidate] if candidate.is_absolute() else [Path.cwd() / candidate, cwd / candidate]
        for path in paths:
            resolved = path.resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        raise typer.BadParameter(f"binary not found: {binary}")

    local_bin = cwd / "node_modules" / ".bin" / binary
    if local_bin.exists() and local_bin.is_file():
        return local_bin.resolve()

    found = shutil.which(binary)
    if found:
        return Path(found).resolve()

    raise typer.BadParameter(f"binary not found on PATH or node_modules/.bin: {binary}")


def _prepare_sandbox_binary(
    resolved_binary: Path,
    sandbox_paths: "SandboxPaths",
    agent_id: str,
) -> tuple["SandboxPaths", Path, tempfile.TemporaryDirectory[str] | None]:
    if _is_sandbox_system_binary(resolved_binary):
        return sandbox_paths, resolved_binary, None

    if sandbox_paths.host_root_dir is not None:
        host_root = Path(sandbox_paths.host_root_dir)
        if not _is_relative_to(resolved_binary, host_root):
            raise typer.BadParameter("--binary must be inside --root-dir")
        return (
            sandbox_paths,
            _sandbox_workspace_path(resolved_binary, host_root, agent_id),
            None,
        )

    staging_dir = tempfile.TemporaryDirectory(prefix="ageos-workspace-")
    host_root = Path(staging_dir.name)
    staged_binary = host_root / resolved_binary.name
    shutil.copy2(resolved_binary, staged_binary)
    log_debug("staged sandbox binary", f"source={resolved_binary} dest={staged_binary}")
    staged_paths = SandboxPaths(
        host_workdir=host_root,
        sandbox_workdir="/workspace",
        host_root_dir=str(host_root),
    )
    return (
        staged_paths,
        _sandbox_workspace_path(staged_binary, host_root, agent_id),
        staging_dir,
    )


def _sandbox_workspace_path(path: Path, host_root: Path, agent_id: str) -> Path:
    relative = path.relative_to(host_root)
    return Path("/home") / agent_id / "workspace" / relative


def _is_sandbox_system_binary(path: Path) -> bool:
    resolved = path.resolve()
    return any(_is_relative_to(resolved, prefix) for prefix in _SANDBOX_SYSTEM_PREFIXES)


def _argv_for_binary(binary: Path | str) -> list[str]:
    binary_path = Path(binary)
    binary_text = binary_path.as_posix()
    if binary_path.suffix == ".py":
        return [_ageos_python(), binary_text]
    return [binary_text]


def _ageos_python() -> str:
    ageos_python = Path(os.environ.get("AGEOS_PYTHON", "/opt/ageos/bin/python"))
    return str(ageos_python if ageos_python.exists() else Path(sys.executable))


def _ageos_site_packages() -> Path | None:
    candidates: list[Path] = []
    install_prefix = Path(os.environ.get("AGEOS_PREFIX", "/opt/ageos"))
    lib_dir = install_prefix / "lib"
    if lib_dir.is_dir():
        candidates.extend(sorted(lib_dir.glob("python*/site-packages"), reverse=True))
    runtime_site = Path(sys.prefix) / "lib"
    if runtime_site.is_dir():
        candidates.extend(sorted(runtime_site.glob("python*/site-packages"), reverse=True))
    for candidate in candidates:
        if (candidate / "ageos").is_dir():
            return candidate
    return None


def _run_native_sandbox(
    client: SchedulerClient,
    target_args: list[str],
    *,
    memory: str,
    cpu: int,
    niceness: int,
    workdir: str,
    root_dir: str | None,
    rootfs_dir: str | None,
    overlay_upper_dir: str | None,
    overlay_work_dir: str | None,
    env: dict[str, str],
    isolate_network: bool,
    inference_host: str | None = None,
    inference_port: int = 0,
    sandbox_inference_port: int = 0,
) -> int:
    if not target_args:
        raise typer.BadParameter("missing sandbox command")
    original_env = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        return client.native.run_sandbox(
            target_args[0],
            target_args,
            resource_niceness=niceness,
            memory_max=_parse_bytes(memory),
            cpu_percent=cpu,
            workdir=workdir,
            root_dir=root_dir,
            rootfs_dir=rootfs_dir,
            overlay_upper_dir=overlay_upper_dir,
            overlay_work_dir=overlay_work_dir,
            isolate_network=isolate_network,
            inference_host=inference_host,
            inference_port=inference_port,
            sandbox_inference_port=sandbox_inference_port,
        )
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def _parse_bytes(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        raise typer.BadParameter("memory limit cannot be empty")
    suffix = stripped[-1].lower()
    number = stripped[:-1] if suffix in {"g", "m"} else stripped
    try:
        base = int(number)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid memory limit: {value}") from exc
    if suffix == "g":
        return base * 1024 * 1024 * 1024
    if suffix == "m":
        return base * 1024 * 1024
    return base


class SandboxPaths:
    def __init__(self, host_workdir: Path, sandbox_workdir: str, host_root_dir: str | None) -> None:
        self.host_workdir = host_workdir
        self.sandbox_workdir = sandbox_workdir
        self.host_root_dir = host_root_dir


def _resolve_sandbox_paths(root_dir: Path | None, workdir: Path | None) -> SandboxPaths:
    if root_dir is None:
        return SandboxPaths(host_workdir=workdir or Path.cwd(), sandbox_workdir="/workspace", host_root_dir=None)
    resolved_root = root_dir.expanduser().resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise typer.BadParameter(f"root directory not found: {root_dir}")
    _validate_writable_root(resolved_root)
    resolved_workdir = (workdir.expanduser().resolve() if workdir is not None else resolved_root)
    if not _is_relative_to(resolved_workdir, resolved_root):
        raise typer.BadParameter("--workdir must be inside --root-dir")
    relative_workdir = resolved_workdir.relative_to(resolved_root)
    sandbox_workdir = "/workspace"
    if relative_workdir != Path("."):
        sandbox_workdir = f"/workspace/{relative_workdir.as_posix()}"
    return SandboxPaths(
        host_workdir=resolved_workdir,
        sandbox_workdir=sandbox_workdir,
        host_root_dir=str(resolved_root),
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_writable_root(root: Path) -> None:
    protected_roots = [
        Path("/"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/opt"),
        Path("/etc"),
        Path("/var"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
        Path("/run"),
    ]
    for protected in protected_roots:
        if root == protected or (protected != Path("/") and _is_relative_to(root, protected)):
            raise typer.BadParameter(f"--root-dir cannot be inside protected system path: {protected}")
    source_root = _source_checkout_root()
    if source_root is not None and (
        root == source_root
        or _is_relative_to(source_root, root)
        or (_is_relative_to(root, source_root) and not _is_allowed_source_workspace(root, source_root))
    ):
        raise typer.BadParameter("--root-dir cannot include the AgeOS application source tree")
    source_tree = _ageos_source_tree_for(root)
    if source_tree is not None and not _is_allowed_source_workspace(root, source_tree):
        raise typer.BadParameter("--root-dir cannot be inside the AgeOS application source tree")


def _source_checkout_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists() and (candidate / "ageos").is_dir():
        return candidate
    return None


def _ageos_source_tree_for(root: Path) -> Path | None:
    for candidate in (root, *root.parents):
        if (
            (candidate / "pyproject.toml").exists()
            and (candidate / "ageos" / "cli" / "run.py").exists()
            and (candidate / "libageos" / "sandbox.c").exists()
        ):
            return candidate
    return None


def _is_allowed_source_workspace(root: Path, source_root: Path) -> bool:
    examples = source_root / "examples"
    return root == examples or _is_relative_to(root, examples)


def _apply_sandbox_inference_env(env: dict[str, str], endpoint: SandboxInferenceEndpoint) -> None:
    env["AGEOS_API_BASE_URL"] = endpoint.sandbox_base_url
    env["OPENAI_BASE_URL"] = f"{endpoint.sandbox_base_url}/v1"
    env["AGEOS_SANDBOX_INFERENCE_HOST"] = "127.0.0.1"
    env["AGEOS_SANDBOX_INFERENCE_PORT"] = str(endpoint.sandbox_port)
    env["AGEOS_NETWORK"] = "inference-only"


class SandboxInferenceEndpoint:
    def __init__(self, host: str, host_port: int, sandbox_port: int) -> None:
        self.host = host
        self.host_port = host_port
        self.sandbox_port = sandbox_port
        self.sandbox_base_url = f"http://127.0.0.1:{sandbox_port}"


def _sandbox_inference_endpoint(host_base_url: str) -> SandboxInferenceEndpoint:
    parsed = urlparse(host_base_url)
    if parsed.scheme not in {"http", ""}:
        raise typer.BadParameter("sandboxed inference endpoint must use HTTP")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    return SandboxInferenceEndpoint(host=host, host_port=port, sandbox_port=port)
