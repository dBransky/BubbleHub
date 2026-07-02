from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_bubblehub_user_config(request: pytest.FixtureRequest, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent of the developer's BubbleHub config."""

    if request.node.get_closest_marker("integration") is not None:
        return

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BUBBLEHUB_MODELS_CONFIG", raising=False)
