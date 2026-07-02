from unittest.mock import patch

from bubblehub.native import _has_sandbox_agent_uid, _has_sandbox_user_namespace, is_sandboxed


def test_has_sandbox_user_namespace_detects_single_uid_mapping() -> None:
    uid_map = "0 54635 1\n"

    with patch("pathlib.Path.read_text", return_value=uid_map):
        assert _has_sandbox_user_namespace() is True


def test_is_sandboxed_uses_namespace_even_without_env(monkeypatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)

    with patch("bubblehub.native._has_sandbox_user_namespace", return_value=True):
        assert is_sandboxed() is True


def test_is_sandboxed_uses_agent_uid_even_without_env(monkeypatch) -> None:
    monkeypatch.delenv("BUBBLEHUB_SANDBOX", raising=False)

    with patch("os.geteuid", return_value=60042):
        assert _has_sandbox_agent_uid() is True
        assert is_sandboxed() is True


def test_has_sandbox_user_namespace_treats_proc_permission_denied_for_root_as_sandbox() -> None:
    with (
        patch("pathlib.Path.read_text", side_effect=PermissionError),
        patch("os.geteuid", return_value=0),
    ):
        assert _has_sandbox_user_namespace() is True
