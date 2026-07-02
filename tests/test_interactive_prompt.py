from __future__ import annotations

import io

from bubblehub.cli.interactive import choose_option


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
