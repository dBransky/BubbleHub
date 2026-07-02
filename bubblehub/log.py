from __future__ import annotations

import inspect
import os
import sys
from enum import IntEnum
from pathlib import Path
from typing import IO, TextIO

__all__ = [
    "configure_logging",
    "extract_global_log_options",
    "log_debug",
    "log_error",
    "log_info",
]


class _LogLevel(IntEnum):
    ERROR = 0
    INFO = 1
    DEBUG = 2


_current_level = _LogLevel.ERROR
_configured = False
_log_file_handle: TextIO | None = None


def extract_global_log_options(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    """Remove global log options from argv regardless of position (before or after the command)."""

    log_level: str | None = None
    log_file: str | None = None
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            cleaned.extend(argv[index:])
            break
        if arg == "--log-level":
            if index + 1 >= len(argv):
                raise ValueError("--log-level requires a value")
            log_level = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--log-level="):
            log_level = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--log-file":
            if index + 1 >= len(argv):
                raise ValueError("--log-file requires a path")
            log_file = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--log-file="):
            log_file = arg.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    return cleaned, log_level, log_file


def configure_logging(level: str | None = None, log_file: str | Path | None = None) -> str:
    """Configure BubbleHub log verbosity and optional log file output."""

    global _current_level, _configured
    raw = (level or os.environ.get("BUBBLEHUB_LOG_LEVEL", "error")).strip().lower()
    if raw == "debug":
        resolved = "debug"
        _current_level = _LogLevel.DEBUG
    elif raw == "info":
        resolved = "info"
        _current_level = _LogLevel.INFO
    else:
        resolved = "error"
        _current_level = _LogLevel.ERROR
    os.environ["BUBBLEHUB_LOG_LEVEL"] = resolved
    _configured = True
    _configure_log_file(log_file)
    _sync_native_logging(resolved)
    return resolved


def log_error(text: str, *params: object) -> None:
    _emit("ERROR", _LogLevel.ERROR, text, *params)


def log_info(text: str, *params: object) -> None:
    _emit("INFO", _LogLevel.INFO, text, *params)


def log_debug(text: str, *params: object) -> None:
    _emit("DEBUG", _LogLevel.DEBUG, text, *params)


def _configure_log_file(log_file: str | Path | None) -> None:
    global _log_file_handle
    if _log_file_handle is not None:
        _log_file_handle.close()
        _log_file_handle = None

    resolved = log_file
    if resolved is None:
        env_file = os.environ.get("BUBBLEHUB_LOG_FILE")
        if env_file:
            resolved = env_file

    if not resolved:
        os.environ.pop("BUBBLEHUB_LOG_FILE", None)
        _sync_native_logging(os.environ.get("BUBBLEHUB_LOG_LEVEL", "error"))
        return

    path = Path(resolved).expanduser()
    if _is_sandboxed() and not _is_allowed_sandbox_log_file(path):
        os.environ.pop("BUBBLEHUB_LOG_FILE", None)
        _sync_native_logging(os.environ.get("BUBBLEHUB_LOG_LEVEL", "error"))
        return

    try:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        _log_file_handle = path.open("a", encoding="utf-8")
    except OSError:
        os.environ.pop("BUBBLEHUB_LOG_FILE", None)
        _sync_native_logging(os.environ.get("BUBBLEHUB_LOG_LEVEL", "error"))
        return

    os.environ["BUBBLEHUB_LOG_FILE"] = str(path)
    _sync_native_logging(os.environ.get("BUBBLEHUB_LOG_LEVEL", "error"))


def _emit(level_name: str, level: _LogLevel, text: str, *params: object) -> None:
    if not _should_emit(level):
        return
    filename, line = _caller_location()
    message = f"{level_name} {filename}:{line} {text}"
    if params:
        message = f"{message}:{_format_params(*params)}"
    sink: IO[str] = _log_file_handle if _log_file_handle is not None else sys.stderr
    print(message, file=sink, flush=True)


def _should_emit(level: _LogLevel) -> bool:
    if level == _LogLevel.ERROR:
        return True
    if not _configured:
        configure_logging()
    return _current_level >= level


def _caller_location() -> tuple[str, int]:
    for frame_info in inspect.stack()[1:]:
        path = Path(frame_info.filename)
        if path.name == "log.py" and path.parent.name == "bubblehub":
            continue
        return path.name, frame_info.lineno
    return "unknown", 0


def _format_params(*params: object) -> str:
    return " ".join(str(param) for param in params)


def _is_sandboxed() -> bool:
    if os.environ.get("BUBBLEHUB_SANDBOX") == "1":
        return True
    try:
        from bubblehub.native import is_sandboxed

        return is_sandboxed()
    except Exception:
        return False


def _sandbox_log_roots() -> list[Path]:
    roots: list[Path] = []
    for name in ("BUBBLEHUB_AGENT_HOME", "BUBBLEHUB_WORKSPACE", "TMPDIR", "HOME"):
        value = os.environ.get(name)
        if not value:
            continue
        try:
            roots.append(Path(value).resolve())
        except OSError:
            continue
    if _is_sandboxed():
        try:
            roots.append(Path("/workspace").resolve())
        except OSError:
            pass
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _is_allowed_sandbox_log_file(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return False
    for root in _sandbox_log_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _sync_native_logging(level: str) -> None:
    try:
        from bubblehub.native import sync_log_config

        sync_log_config(level, os.environ.get("BUBBLEHUB_LOG_FILE"))
    except Exception:
        return
