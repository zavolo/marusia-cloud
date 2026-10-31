from __future__ import annotations

import logging
import os
import sys

import structlog


def setup(level: str = "INFO") -> None:
    json_mode = os.environ.get("MARUSIA_LOG_JSON") == "1"

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    structlog.configure(
        processors=shared + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (structlog.processors.JSONRenderer() if json_mode
                else structlog.dev.ConsoleRenderer(colors=False))
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def get_logger(name: str = "emu"):
    return structlog.get_logger(name)
