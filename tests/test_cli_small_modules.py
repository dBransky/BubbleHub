from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from bubblehub.cli import inference_daemon, poc, prompt
from bubblehub.engine.downloader import DownloadError, HfDownloader
from bubblehub.engine.registry import ModelSpec


def _model(*, filename: str | None = "model.gguf") -> ModelSpec:
    return ModelSpec(
        name="small",
        flavor="qwen",
        capability="instruct",
        tier="small",
        backend="llama",
        repo_id="repo/small",
        filename=filename,
        ram_gb=4,
        vram_gb=0,
    )


def test_inference_daemon_loads_config_and_runs_api() -> None:
    config = SimpleNamespace(host="127.0.0.1", port=8011, default_specialty="default")

    with (
        patch("bubblehub.cli.inference_daemon.configure_logging") as configure,
        patch("bubblehub.cli.inference_daemon.load_inference_config", return_value=config),
        patch("bubblehub.cli.inference_daemon.run_http_api") as run_api,
    ):
        status = inference_daemon.main()

    assert status == 0
    configure.assert_called_once()
    api_config = run_api.call_args.args[0]
    assert (api_config.host, api_config.port, api_config.default_specialty) == ("127.0.0.1", 8011, "default")


def test_downloader_returns_cached_file_and_snapshot_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_model = _model()
    file_path = tmp_path / "models" / "small" / "model.gguf"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("cached", encoding="utf-8")
    assert HfDownloader(tmp_path).ensure_model(file_model) == file_path

    dir_model = _model(filename=None)
    (tmp_path / "models" / "small" / "weights.bin").write_text("cached", encoding="utf-8")
    assert HfDownloader(tmp_path).ensure_model(dir_model) == tmp_path / "models" / "small"

    monkeypatch.setenv("BUBBLEHUB_CACHE", str(tmp_path))
    assert HfDownloader().cache_dir == tmp_path


def test_downloader_uses_huggingface_helpers_and_wraps_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = Mock()
    fake_module.hf_hub_download.return_value = str(tmp_path / "downloaded.gguf")
    fake_module.snapshot_download.return_value = str(tmp_path / "snapshot")
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)
    monkeypatch.setenv("BUBBLEHUB_VALIDATE_MODEL_CACHE", "1")

    assert HfDownloader(tmp_path).ensure_model(_model()) == tmp_path / "downloaded.gguf"
    assert HfDownloader(tmp_path).ensure_model(_model(filename=None)) == tmp_path / "snapshot"

    fake_module.hf_hub_download.side_effect = RuntimeError("denied")
    with pytest.raises(DownloadError, match="failed to download repo/small"):
        HfDownloader(tmp_path).ensure_model(_model())


def test_downloader_reports_missing_huggingface_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def blocked_import(name: str, *args, **kwargs):
        if name == "huggingface_hub":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked_import)

    with pytest.raises(DownloadError, match="huggingface-hub is required"):
        HfDownloader(tmp_path).ensure_model(_model())


def test_structured_prompt_retries_and_writes_output(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    output = tmp_path / "out.json"
    schema.write_text('{"answer": "example"}', encoding="utf-8")
    chat = Mock(side_effect=["not json", '{"answer": "fixed"}'])

    payload = prompt._run_prompt("default", schema, "question", chat)

    assert payload == '{\n  "answer": "fixed"\n}'
    assert chat.call_count == 2

    with patch("bubblehub.cli.prompt.EngineSession") as session_cls:
        session_cls.return_value.__enter__.return_value.chat.return_value = "plain"
        prompt.command("default", None, "question", 5, output)

    assert output.read_text(encoding="utf-8") == "plain\n"


def test_poc_repl_handles_blank_prompt_answer_quit_and_interrupt() -> None:
    session = Mock()
    session.chat.return_value = "answer"

    with (
        patch("bubblehub.cli.poc.EngineSession") as session_cls,
        patch("builtins.input", side_effect=["", "hello", ":quit"]),
    ):
        session_cls.return_value.__enter__.return_value = session
        poc.command("default", niceness=1, flavor="qwen", capability="instruct")

    session.chat.assert_called_once_with([{"role": "user", "content": "hello"}])
    session_cls.assert_called_once_with(
        "default",
        niceness=1,
        flavor="qwen",
        capability="instruct",
        status_callback=session_cls.call_args.kwargs["status_callback"],
    )

    with (
        patch("bubblehub.cli.poc.load_inference_config", return_value=SimpleNamespace(default_specialty="default")),
        patch("bubblehub.cli.poc.EngineSession"),
        patch("builtins.input", side_effect=KeyboardInterrupt),
    ):
        poc.command(None, niceness=0, flavor=None, capability=None)
