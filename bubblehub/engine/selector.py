from __future__ import annotations

from dataclasses import dataclass

from bubblehub.native import HardwareInfo


@dataclass(frozen=True)
class MachineTier:
    name: str
    order: list[str]


def select_tier(hardware: HardwareInfo) -> MachineTier:
    ram_gb = hardware.ram_bytes / 1024**3
    vram_gb = hardware.vram_bytes / 1024**3
    if ram_gb < 8:
        return MachineTier("tiny", ["tiny"])
    if vram_gb >= 16 or ram_gb >= 32:
        return MachineTier("large", ["large", "medium", "small", "tiny"])
    if ram_gb >= 16:
        return MachineTier("medium", ["medium", "small", "tiny"])
    return MachineTier("small", ["small", "tiny"])
