"""Layer 4 — structured logging configuration via structlog.

Named ``log_config.py`` (not ``logging.py``) to avoid shadowing the stdlib ``logging`` module.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "raw_payload",
        "access_token",
        "refresh_token",
        "account_number",
        "iban",
    }
)


def redact_sensitive(
    logger: Any,
    method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor: replace sensitive key values with ``'<redacted>'``."""
    for key in SENSITIVE_KEYS:
        if key in event_dict:
            event_dict[key] = "<redacted>"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog with console rendering and sensitive-field redaction."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_sensitive,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a named structlog bound logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]
