from __future__ import annotations

import io
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bubblehub import log as bubblehub_log
from bubblehub.cli.main import app
from bubblehub.log import extract_global_log_options


@pytest.fixture(autouse=True)
def reset_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_LOG_FILE", raising=False)
    bubblehub_log.configure_logging("error")


def test_error_always_prints(capsys: io.StringIO) -> None:
    bubblehub_log.configure_logging("error")
    bubblehub_log.log_error("boom", "detail")
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "boom:detail" in captured.err
    assert "test_log.py:" in captured.err


def test_info_hidden_at_error_level(capsys: io.StringIO) -> None:
    bubblehub_log.configure_logging("error")
    bubblehub_log.log_info("visible only at info")
    assert capsys.readouterr().err == ""


def test_info_prints_at_info_level(capsys: io.StringIO) -> None:
    bubblehub_log.configure_logging("info")
    bubblehub_log.log_info("hello", "world")
    captured = capsys.readouterr()
    assert "INFO" in captured.err
    assert "hello:world" in captured.err


def test_debug_prints_at_debug_level(capsys: io.StringIO) -> None:
    bubblehub_log.configure_logging("debug")
    bubblehub_log.log_debug("trace", "step=1")
    captured = capsys.readouterr()
    assert "DEBUG" in captured.err
    assert "trace:step=1" in captured.err


def test_configure_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_LOG_LEVEL", raising=False)
    resolved = bubblehub_log.configure_logging("info")
    assert resolved == "info"
    import os

    assert os.environ["BUBBLEHUB_LOG_LEVEL"] == "info"


def test_extract_log_options_after_subcommand() -> None:
    cleaned, level, log_file = extract_global_log_options(["poc", "--log-level", "debug"])
    assert cleaned == ["poc"]
    assert level == "debug"
    assert log_file is None


def test_extract_log_options_before_subcommand() -> None:
    cleaned, level, log_file = extract_global_log_options(["--log-level", "info", "serve"])
    assert cleaned == ["serve"]
    assert level == "info"
    assert log_file is None


def test_extract_log_file_option() -> None:
    cleaned, level, log_file = extract_global_log_options(["run", "--binary", "agent", "--log-file", "/tmp/bubblehub.log", "--log-level", "debug"])
    assert cleaned == ["run", "--binary", "agent"]
    assert level == "debug"
    assert log_file == "/tmp/bubblehub.log"


def test_extract_preserves_args_after_double_dash() -> None:
    cleaned, level, log_file = extract_global_log_options(["run", "--binary", "agent", "--", "tool", "--log-level", "debug"])
    assert cleaned == ["run", "--binary", "agent", "--", "tool", "--log-level", "debug"]
    assert level is None
    assert log_file is None


def test_log_file_redirect(tmp_path: Path) -> None:
    log_path = tmp_path / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("saved", "ok")
    text = log_path.read_text(encoding="utf-8")
    assert "saved:ok" in text


def test_log_file_does_not_write_to_stderr(tmp_path: Path, capsys: io.StringIO) -> None:
    log_path = tmp_path / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("file only", "message")
    bubblehub_log.log_error("file error", "detail")

    captured = capsys.readouterr()
    assert captured.err == ""
    text = log_path.read_text(encoding="utf-8")
    assert "file only:message" in text
    assert "file error:detail" in text


def test_log_file_respects_log_level(tmp_path: Path) -> None:
    log_path = tmp_path / "bubblehub.log"
    bubblehub_log.configure_logging("error", log_path)
    bubblehub_log.log_info("hidden info")
    bubblehub_log.log_debug("hidden debug")
    bubblehub_log.log_error("visible error", "boom")

    text = log_path.read_text(encoding="utf-8")
    assert "hidden info" not in text
    assert "hidden debug" not in text
    assert "visible error:boom" in text


def test_log_file_appends(tmp_path: Path) -> None:
    log_path = tmp_path / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("first", "line")
    bubblehub_log.log_info("second", "line")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "first:line" in lines[0]
    assert "second:line" in lines[1]


def test_log_file_sets_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.delenv("BUBBLEHUB_LOG_FILE", raising=False)
    log_path = tmp_path / "nested" / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    assert os.environ["BUBBLEHUB_LOG_FILE"] == str(log_path)


def test_log_file_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "from-env.log"
    monkeypatch.setenv("BUBBLEHUB_LOG_FILE", str(log_path))
    bubblehub_log.configure_logging("info")
    bubblehub_log.log_info("env sink", "works")

    assert log_path.read_text(encoding="utf-8").strip().endswith("env sink:works")


def test_log_file_creates_parent_directories(tmp_path: Path) -> None:
    log_path = tmp_path / "deep" / "nested" / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("created", "dirs")

    assert log_path.is_file()
    assert "created:dirs" in log_path.read_text(encoding="utf-8")


def test_extract_log_file_equals_form() -> None:
    cleaned, level, log_file = extract_global_log_options(["serve", "--log-file=/tmp/custom.log", "--log-level=debug"])
    assert cleaned == ["serve"]
    assert level == "debug"
    assert log_file == "/tmp/custom.log"


def test_extract_log_file_requires_value() -> None:
    with pytest.raises(ValueError, match="--log-file requires a path"):
        extract_global_log_options(["poc", "--log-file"])


def test_run_cli_writes_logs_to_file(tmp_path: Path) -> None:
    import sys

    log_path = tmp_path / "cli.log"
    previous = sys.argv
    sys.argv = ["bubblehub", "poc", "--log-level", "debug", "--log-file", str(log_path), "-h"]
    try:
        from bubblehub.cli.main import run_cli

        with pytest.raises(SystemExit):
            run_cli()
    finally:
        sys.argv = previous

    text = log_path.read_text(encoding="utf-8")
    assert "bubblehub cli initialized" in text
    assert "log_level=debug" in text


def test_reconfigure_without_log_file_returns_to_stderr(
    tmp_path: Path,
    capsys: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    log_path = tmp_path / "bubblehub.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("in file")

    monkeypatch.delenv("BUBBLEHUB_LOG_FILE", raising=False)
    bubblehub_log.configure_logging("info")
    bubblehub_log.log_info("on stderr")

    assert "in file" in log_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert "on stderr" in captured.err
    assert "BUBBLEHUB_LOG_FILE" not in os.environ


def test_native_logs_redirect_to_file(tmp_path: Path, capsys: io.StringIO) -> None:
    import ctypes

    from bubblehub.native import LibBubbleHubError, _bytes, _load_libbubblehub

    try:
        lib = _load_libbubblehub()
        lib.bubblehub_log_set_file
    except (LibBubbleHubError, AttributeError):
        pytest.skip("libbubblehub logging symbols are unavailable")

    log_path = tmp_path / "native.log"
    lib.bubblehub_log_set_level.argtypes = [ctypes.c_char_p]
    lib.bubblehub_log_set_level.restype = None
    lib.bubblehub_log_set_file.argtypes = [ctypes.c_char_p]
    lib.bubblehub_log_set_file.restype = None
    lib.bubblehub_log_write.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
    ]
    lib.bubblehub_log_write.restype = None

    lib.bubblehub_log_set_level(b"debug")
    lib.bubblehub_log_set_file(_bytes(str(log_path)))
    lib.bubblehub_log_write(
        2,
        b"scheduler.c",
        625,
        b"evicted model",
        b"name=%s",
        b"test-model",
    )

    assert "evicted model:name=test-model" in log_path.read_text(encoding="utf-8")
    assert capsys.readouterr().err == ""


def test_configure_logging_syncs_native_log_file(tmp_path: Path, capsys: io.StringIO) -> None:
    import ctypes

    from bubblehub.native import LibBubbleHubError, _load_libbubblehub

    try:
        lib = _load_libbubblehub()
        lib.bubblehub_log_set_file
    except (LibBubbleHubError, AttributeError):
        pytest.skip("libbubblehub logging symbols are unavailable")

    log_path = tmp_path / "both.log"
    bubblehub_log.configure_logging("debug", log_path)
    bubblehub_log.log_info("python line", "synced")
    lib.bubblehub_log_write.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_char_p,
    ]
    lib.bubblehub_log_write.restype = None
    lib.bubblehub_log_write(1, b"scheduler.c", 591, b"marked model loaded", b"name=%s", b"mistral")

    text = log_path.read_text(encoding="utf-8")
    assert "python line:synced" in text
    assert "marked model loaded:name=mistral" in text
    assert capsys.readouterr().err == ""


def test_version_works_after_log_option_extraction() -> None:
    cleaned, level, _ = extract_global_log_options(["--version", "--log-level", "debug"])
    assert cleaned == ["--version"]
    assert level == "debug"
    bubblehub_log.configure_logging(level)
    result = CliRunner().invoke(app, cleaned)
    assert result.exit_code == 0
    assert "bubblehub" in result.output


def test_sandbox_rejects_host_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: io.StringIO) -> None:
    monkeypatch.setenv("BUBBLEHUB_SANDBOX", "1")
    monkeypatch.setenv("BUBBLEHUB_AGENT_HOME", "/home/agt-test")
    monkeypatch.setenv("BUBBLEHUB_WORKSPACE", "/home/agt-test/workspace")
    monkeypatch.setenv("TMPDIR", "/home/agt-test/tmp")
    host_log = tmp_path.parent / "outside-sandbox" / "host.log"
    bubblehub_log.configure_logging("info", host_log)
    bubblehub_log.log_info("should use stderr")

    assert not host_log.exists()
    assert "should use stderr" in capsys.readouterr().err
    import os

    assert "BUBBLEHUB_LOG_FILE" not in os.environ


def test_sandbox_allows_workspace_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: io.StringIO) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BUBBLEHUB_SANDBOX", "1")
    monkeypatch.setenv("BUBBLEHUB_AGENT_HOME", str(home))
    monkeypatch.setenv("BUBBLEHUB_WORKSPACE", str(workspace))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))

    log_path = workspace / "agent.log"
    bubblehub_log.configure_logging("info", log_path)
    bubblehub_log.log_info("sandbox log", "ok")

    assert "sandbox log:ok" in log_path.read_text(encoding="utf-8")
    assert capsys.readouterr().err == ""
