from __future__ import annotations

import os
from pathlib import Path

from bubblehub.engine.registry import ModelSpec


class DownloadError(RuntimeError):
    pass


class HfDownloader:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path(os.environ.get("BUBBLEHUB_CACHE", "~/.cache/bubblehub")).expanduser()

    def ensure_model(self, model: ModelSpec) -> Path:
        target_dir = self.cache_dir / "models" / model.name
        if model.filename:
            target_file = target_dir / model.filename
            if target_file.exists():
                return target_file
        elif target_dir.exists() and any(target_dir.iterdir()):
            return target_dir

        try:
            from huggingface_hub import hf_hub_download, snapshot_download
        except ImportError as exc:
            raise DownloadError("huggingface-hub is required for auto-download") from exc

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            if model.filename:
                return Path(
                    hf_hub_download(
                        repo_id=model.repo_id,
                        filename=model.filename,
                        local_dir=target_dir,
                    )
                )
            return Path(
                snapshot_download(
                    repo_id=model.repo_id,
                    local_dir=target_dir,
                )
            )
        except Exception as exc:  # noqa: BLE001 - keep first-run error actionable.
            raise DownloadError(
                f"failed to download {model.repo_id}; set HF_TOKEN for gated models or register a local path in ~/.config/bubblehub/models.yaml"
            ) from exc
