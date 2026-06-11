"""Logging configuration for nonoka-cli using structlog."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(
  level: int = logging.INFO,
  log_file: Path | None = None,
  console: bool = False,
) -> structlog.stdlib.BoundLogger:
  """Configure structlog for nonoka-cli.

  Args:
    level: Logging level (default: INFO).
    log_file: Path to log file. If None, defaults to
      ~/.local/share/nonoka/logs/nonoka-cli.log.
    console: Whether to also output logs to stderr.

  Returns:
    A configured structlog logger.
  """
  # Default log file path
  if log_file is None:
    log_file = Path.home() / ".local" / "share" / "nonoka" / "logs" / "nonoka-cli.log"

  # Ensure log directory exists
  log_file.parent.mkdir(parents=True, exist_ok=True)

  shared_processors = [
    structlog.stdlib.filter_by_level,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
  ]

  handlers: list[logging.Handler] = [
    logging.FileHandler(log_file, encoding="utf-8"),
  ]

  if console:
    handlers.append(logging.StreamHandler(sys.stderr))

  logging.basicConfig(
    format="%(message)s",
    level=level,
    handlers=handlers,
  )

  structlog.configure(
    processors=shared_processors + [
      structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
  )

  # Configure formatter for stdlib handlers
  formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.dev.ConsoleRenderer(colors=console),
    foreign_pre_chain=shared_processors,
  )
  for handler in handlers:
    handler.setFormatter(formatter)

  return structlog.get_logger("nonoka_cli")
