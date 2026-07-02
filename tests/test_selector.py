from bubblehub.engine.selector import select_tier
from bubblehub.native import HardwareInfo


def test_selects_tiny_under_8gb_ram() -> None:
    assert select_tier(HardwareInfo(ram_bytes=4 * 1024**3, vram_bytes=0)).name == "tiny"


def test_selects_large_for_big_vram() -> None:
    tier = select_tier(HardwareInfo(ram_bytes=16 * 1024**3, vram_bytes=24 * 1024**3))
    assert tier.name == "large"
    assert tier.order[0] == "large"
