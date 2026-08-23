"""Loguru-based structured logging configuration for EcoGuardAI."""
from __future__ import annotations

import sys
import logging
import io
from loguru import logger

# Ensure stdout/stderr use UTF-8 encoding to avoid UnicodeEncodeError on Windows (cp1252 default)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


class _InterceptHandler(logging.Handler):
    """Route standard-library logging records into Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno  # type: ignore[assignment]

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_level: str = "INFO", json_format: bool = False) -> None:
    """Configure Loguru sinks.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_format: When True, emit JSON-serialised log records (production).
    """
    logger.remove()  # remove default sink

    if json_format:
        logger.add(
            sys.stdout,
            level=log_level,
            serialize=True,          # JSON output
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
        logger.add(
            "logs/ecoguardai_{time:YYYY-MM-DD}.log",
            level=log_level,
            serialize=True,
            rotation="100 MB",
            retention="30 days",
            compression="gz",
            enqueue=True,
        )
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )
        logger.add(
            sys.stdout,
            format=fmt,
            level=log_level,
            colorize=True,
            enqueue=True,
        )
        logger.add(
            "logs/ecoguardai_{time:YYYY-MM-DD}.log",
            format=fmt,
            level=log_level,
            rotation="50 MB",
            retention="14 days",
            compression="gz",
            enqueue=True,
        )

    # Intercept stdlib loggers (uvicorn, sqlalchemy, etc.)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [_InterceptHandler()]
        std_logger.propagate = False

    logger.info("Logging configured | level={} json={}", log_level, json_format)