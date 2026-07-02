from __future__ import annotations

from bubblehub.http_api import ApiConfig, run_http_api
from bubblehub.inference import load_inference_config
from bubblehub.log import configure_logging, log_info


def main() -> int:
    configure_logging()
    config = load_inference_config()
    log_info("inference daemon starting", f"{config.host}:{config.port}")
    run_http_api(
        ApiConfig(
            host=config.host,
            port=config.port,
            default_specialty=config.default_specialty,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
