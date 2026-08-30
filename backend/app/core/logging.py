"""
Structured logging configuration for the Gateway Control Center.

Provides a consistent logger setup that future services can reuse.
Console output in development; structured enough for future event logging.
Never logs secret values.
"""

from __future__ import annotations

import logging
import sys
from typing import Any


_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger for the application."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates on reload
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Use this in all service modules."""
    return logging.getLogger(f"gcc.{name}")


def mask_secret(value: str | None, show: int = 4) -> str:
    """Return a masked representation of a secret.

    Example: mask_secret("sk-abc123456789", show=4) -> "********6789"
    """
    if not value or len(value) <= show:
        return "****"
    return "*" * (len(value) - show) + value[-show:]
