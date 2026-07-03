from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from bubblehub.cli.interactive import _read_prompt_key, _render_options, choose_option


def test_choose_option_renders_green_selection() -> None:
    output = io.StringIO()

    selected = choose_option(
        title="BubbleHub base model",
        message="Pick one.",
        options=("medium (recommended)", "small"),
        default_index=0,
        input_stream=io.StringIO("\x1b[B\n"),
        output_stream=output,
    )

    assert selected == 1
    rendered = output.getvalue()
    assert "Use Up/Down arrows and press Enter." in rendered
    assert "\x1b[32m> small\x1b[0m" in rendered


def test_choose_option_validates_options_and_default() -> None:
    with pytest.raises(ValueError, match="at least one option"):
        choose_option(title="Title", message="Message", options=())
    with pytest.raises(ValueError, match="default_index"):
        choose_option(title="Title", message="Message", options=("one",), default_index=2)


def test_choose_option_keyboard_shortcuts() -> None:
    assert (
        choose_option(
            title="Title",
            message="Message",
            options=("yes", "no"),
            input_stream=io.StringIO("n"),
            output_stream=io.StringIO(),
        )
        == 1
    )
    assert (
        choose_option(
            title="Title",
            message="Message",
            options=("yes", "no"),
            input_stream=io.StringIO("y"),
            output_stream=io.StringIO(),
        )
        == 0
    )
    assert (
        choose_option(
            title="Title",
            message="Message",
            options=("one", "two", "three"),
            input_stream=io.StringIO("3"),
            output_stream=io.StringIO(),
        )
        == 2
    )
    assert (
        choose_option(
            title="Title",
            message="Message",
            options=("one", "two", "three"),
            default_index=0,
            input_stream=io.StringIO("k\n"),
            output_stream=io.StringIO(),
        )
        == 2
    )


def test_choose_option_tty_restores_terminal_settings() -> None:
    class FakeTty(io.StringIO):
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 123

    fake_input = FakeTty("\n")
    with (
        patch("bubblehub.cli.interactive.termios.tcgetattr", return_value=["original"]) as get_attr,
        patch("bubblehub.cli.interactive.tty.setcbreak") as set_cbreak,
        patch("bubblehub.cli.interactive.termios.tcsetattr") as set_attr,
    ):
        assert choose_option(title="Title", message="Message", options=("one",), input_stream=fake_input, output_stream=io.StringIO()) == 0

    get_attr.assert_called_once_with(123)
    set_cbreak.assert_called_once_with(123)
    set_attr.assert_called_once()


def test_render_options_rewinds_and_read_prompt_escape_variants() -> None:
    output = io.StringIO()
    _render_options(("one", "two"), 1, output, rewind=True)
    rendered = output.getvalue()
    assert rendered.startswith("\x1b[2F")
    assert "\x1b[32m> two\x1b[0m" in rendered

    assert _read_prompt_key(io.StringIO("\x1b[A")) == "\x1b[A"
    assert _read_prompt_key(io.StringIO("\x1bx")) == "\x1bx"
