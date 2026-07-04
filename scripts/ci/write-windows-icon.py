#!/usr/bin/env python3
from __future__ import annotations

import struct
import subprocess
import sys
import zlib
from binascii import crc32
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "assets" / "bubblehub-logo.svg"


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc32(kind + data) & 0xFFFFFFFF)


def write_png_rgba(width: int, height: int, pixels: bytes) -> bytes:
    stride = width * 4
    rows = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(rows, 9))
        + png_chunk(b"IEND", b"")
    )


def blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    sr, sg, sb, sa = src
    dr, dg, db, da = dst
    alpha = sa / 255
    inv = 1 - alpha
    return (
        round(sr * alpha + dr * inv),
        round(sg * alpha + dg * inv),
        round(sb * alpha + db * inv),
        round(255 * alpha + da * inv),
    )


def fallback_png(size: int) -> bytes:
    """Render a compact BubbleHub mark without external SVG dependencies."""
    pixels: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (size * size)
    circles = [
        (0.40, 0.58, 0.31),
        (0.66, 0.30, 0.16),
        (0.78, 0.57, 0.10),
    ]
    for y in range(size):
        for x in range(size):
            nx = (x + 0.5) / size
            ny = (y + 0.5) / size
            color = (0, 0, 0, 0)
            for cx, cy, radius in circles:
                dist = ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5
                if dist <= radius:
                    t = min(1.0, dist / radius)
                    pink = (
                        round(255 * (1 - t) + 247 * t),
                        round(215 * (1 - t) + 106 * t),
                        round(230 * (1 - t) + 164 * t),
                        255,
                    )
                    color = blend(color, pink)
            highlights = [
                (0.31, 0.50, 0.12, 0.035),
                (0.60, 0.21, 0.065, 0.023),
                (0.74, 0.52, 0.045, 0.016),
            ]
            for cx, cy, rx, ry in highlights:
                if ((nx - cx) / rx) ** 2 + ((ny - cy) / ry) ** 2 <= 1:
                    color = blend(color, (255, 255, 255, 230))
            pixels[y * size + x] = color
    return write_png_rgba(size, size, bytes(channel for pixel in pixels for channel in pixel))


def render_png(size: int, tmp_dir: Path) -> bytes:
    if not LOGO.is_file():
        raise SystemExit(f"BubbleHub logo not found: {LOGO}")

    output = tmp_dir / f"bubblehub-logo-{size}.png"
    if which("rsvg-convert"):
        subprocess.run(
            [
                "rsvg-convert",
                "--keep-aspect-ratio",
                "--width",
                str(size),
                "--height",
                str(size),
                "--output",
                str(output),
                str(LOGO),
            ],
            check=True,
        )
    else:
        try:
            import cairosvg

            cairosvg.svg2png(url=str(LOGO), write_to=str(output), output_width=size, output_height=size)
        except Exception:
            return fallback_png(size)
    return output.read_bytes()


def write_ico(output: Path, images: list[tuple[int, bytes]]) -> None:
    offset = 6 + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        width_byte = 0 if size == 256 else size
        entries.extend(struct.pack("<BBBBHHII", width_byte, width_byte, 0, 0, 1, 32, len(data), offset))
        payload.extend(data)
        offset += len(data)
    output.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + bytes(entries) + bytes(payload))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: write-windows-icon.py <output.ico>")

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = output.parent / ".bubblehub-icon-build"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        images = [(size, render_png(size, tmp_dir)) for size in (32, 48, 256)]
        write_ico(output, images)
    finally:
        for path in tmp_dir.glob("bubblehub-logo-*.png"):
            path.unlink(missing_ok=True)
        tmp_dir.rmdir()


if __name__ == "__main__":
    main()
