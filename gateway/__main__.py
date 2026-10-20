"""``python -m gateway``: run the loopback gateway for the WhatsApp bridge."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from gateway.app import GatewayApp, build_app
from sanka.config import ConfigError, load_config
from sanka.secrets import SecretError

LOGGER = logging.getLogger("gateway")

DEFAULT_CONFIG_PATH = "config/sanka.yaml"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def main(argv: list[str] | None = None) -> int:
    """Load config, build the app, and serve until a stop signal."""
    _configure_logging()

    config_path = os.environ.get("SANKA_CONFIG", DEFAULT_CONFIG_PATH)
    try:
        config = load_config(config_path)
        app = build_app(config)
    except (ConfigError, SecretError, ValueError) as exc:
        LOGGER.error("gateway_start_failed: %s", exc)
        return 1

    _install_signal_handlers(app)
    LOGGER.info(
        "gateway_listening host=%s port=%d",
        config.gateway.listen_host,
        app.server.bound_port,
    )
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("gateway_interrupted")
    finally:
        app.stop()
    LOGGER.info("gateway_stopped")
    return 0


def _configure_logging() -> None:
    """Metadata-only logs on stderr."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format=LOG_FORMAT,
    )
    # httpx logs full request URLs at INFO; the gateway records its own
    # metadata and must keep logs free of transport detail.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _install_signal_handlers(app: GatewayApp) -> None:
    """Stop through a helper thread: the signal runs on the serving thread."""

    def handle(signum: int, frame: object) -> None:
        LOGGER.info("gateway_shutdown_requested signal=%d", signum)
        threading.Thread(
            target=app.stop, name="sanka-shutdown", daemon=True
        ).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handle)


if __name__ == "__main__":
    raise SystemExit(main())
