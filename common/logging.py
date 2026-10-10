"""Logs estruturados em JSON.

Todo o código usa ``structlog.get_logger()``. O ``job_id`` (e outros campos de
contexto) entra em todas as linhas via ``bind_contextvars``.
"""

import logging
import logging.config

import structlog


def configure_logging(debug: bool = False) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processor": structlog.processors.JSONRenderer(),
                    "foreign_pre_chain": shared,
                },
            },
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
            "root": {"handlers": ["console"], "level": "DEBUG" if debug else "INFO"},
            "loggers": {
                "django.db.backends": {"level": "INFO"},
                "botocore": {"level": "WARNING"},
                "django.utils.autoreload": {"level": "INFO"},
                "PIL": {"level": "INFO"},
                "s3transfer": {"level": "INFO"},
            },
        }
    )
