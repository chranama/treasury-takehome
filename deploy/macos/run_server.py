"""Start the single-process production server with bounded, content-free logging."""

from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path
from typing import Any

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8081
DEFAULT_LOG_DIR = Path("/Users/chranama-server/treasury-takehome-data/logs")
LOG_MAX_BYTES = 1_048_576
LOG_BACKUP_COUNT = 5


def build_log_config(log_dir: Path) -> dict[str, Any]:
    """Return a logging configuration that never enables request access logs."""

    log_path = log_dir / "label-review.log"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "service": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
            }
        },
        "handlers": {
            "service": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "service",
                "filename": str(log_path),
                "maxBytes": LOG_MAX_BYTES,
                "backupCount": LOG_BACKUP_COUNT,
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["service"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["service"], "level": "INFO", "propagate": False},
            # Access logging stays disabled at both the logger and server layers because its
            # default format includes the source address.
            "uvicorn.access": {
                "handlers": [],
                "level": "CRITICAL",
                "propagate": False,
            },
        },
        "root": {"handlers": ["service"], "level": "INFO"},
    }


def main() -> None:
    previous_umask = os.umask(0o077)
    try:
        log_dir = Path(os.environ.get("TREASURY_LOG_DIR", str(DEFAULT_LOG_DIR))).resolve()
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logging.config.dictConfig(build_log_config(log_dir))

        uvicorn.run(
            "app.main:app",
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            workers=1,
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=20,
            log_config=None,
        )
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
