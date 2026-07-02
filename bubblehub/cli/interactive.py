from __future__ import annotations

import sys
import termios
import tty
from typing import TextIO


def choose_option(
    *,
    title: str,
    message: str,
    options: tuple[str, ...],
    default_index: int = 0,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stderr,
) -> int:
    if not options:
        raise ValueError("at least one option is required")
    if not 0 <= default_index < len(options):
        raise ValueError("default_index out of range")

    output_stream.write(f"\n{title}\n{message}\n\nUse Up/Down arrows and press Enter.\n")
    output_stream.flush()
    selected = default_index

    def choose() -> int:
        nonlocal selected
        rendered = False
        while True:
            _render_options(options, selected, output_stream, rewind=rendered)
            rendered = True
            key = _read_prompt_key(input_stream)
            if key in {"\n", "\r", ""}:
                output_stream.write("\n")
                output_stream.flush()
                return selected
            if key in {"\x1b[A", "k"}:
                selected = (selected - 1) % len(options)
            elif key in {"\x1b[B", "j"}:
                selected = (selected + 1) % len(options)
            elif key.isdigit():
                numeric = int(key) - 1
                if 0 <= numeric < len(options):
                    return numeric
            elif len(options) == 2 and key in {"y", "Y", "1"}:
                return 0
            elif len(options) == 2 and key in {"n", "N", "2"}:
                return 1

    if not input_stream.isatty():
        return choose()
    fileno = input_stream.fileno()
    original = termios.tcgetattr(fileno)
    try:
        tty.setcbreak(fileno)
        return choose()
    finally:
        termios.tcsetattr(fileno, termios.TCSADRAIN, original)


def _render_options(options: tuple[str, ...], selected: int, output_stream: TextIO, *, rewind: bool) -> None:
    if rewind:
        output_stream.write(f"\x1b[{len(options)}F")
    for index, label in enumerate(options):
        marker = ">" if index == selected else " "
        line = f"{marker} {label}"
        if index == selected:
            line = f"\x1b[32m{line}\x1b[0m"
        output_stream.write(f"\x1b[2K\r{line}\n")
    output_stream.flush()


def _read_prompt_key(input_stream: TextIO) -> str:
    key = input_stream.read(1)
    if key == "\x1b":
        second = input_stream.read(1)
        if second == "[":
            third = input_stream.read(1)
            return f"\x1b[{third}"
        return key + second
    return key
