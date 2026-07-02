from types import SimpleNamespace
from unittest.mock import patch

from bubblehub.cli import poc


def test_poc_defaults_to_configured_speciality() -> None:
    with (
        patch("bubblehub.cli.poc.load_inference_config", return_value=SimpleNamespace(default_specialty="default-instruct")),
        patch("bubblehub.cli.poc.EngineSession") as session_cls,
        patch("builtins.input", side_effect=EOFError),
    ):
        poc.command(
            speciality=None,
            niceness=0,
            flavor=None,
            capability=None,
        )

    session_cls.assert_called_once()
    assert session_cls.call_args.args[0] == "default-instruct"
