"""CLI entry point for nonoka-cli."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# LiteLLM tries to fetch a remote price map on import, which causes a
# noticeable startup delay and warning spam. Force local-only mode before
# any downstream import pulls it in.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import structlog

from nonoka_cli.bridge.server import main as server_main
from nonoka_cli.utils.logging import setup_logging

logger = structlog.get_logger("nonoka_cli.cli")


def _build_parser() -> argparse.ArgumentParser:
  """Build the argument parser."""
  parser = argparse.ArgumentParser(
    prog="nonoka-cli",
    description="Terminal frontend for the Nonoka Agent framework",
  )
  parser.add_argument(
    "--config",
    type=Path,
    default=None,
    help="Path to configuration file (default: ~/.config/nonoka/config.yaml)",
  )
  parser.add_argument(
    "--model",
    type=str,
    default=None,
    help="Override the model specified in config",
  )
  parser.add_argument(
    "--verbose", "-v",
    action="store_true",
    help="Output INFO level logs to console",
  )
  parser.add_argument(
    "--debug",
    action="store_true",
    help="Output DEBUG level logs to console",
  )
  parser.add_argument(
    "--server",
    action="store_true",
    dest="server",
    help="Run as an NDJSON server backend for OpenCode",
  )
  return parser


def main() -> int:
  """Main entry point.

  Returns:
    Exit code (0 for success, 1 for error).
  """
  parser = _build_parser()
  args = parser.parse_args()

  if args.server:
    return server_main(config_path=args.config, model=args.model)

  log_level = (
    logging.DEBUG if args.debug
    else logging.INFO if args.verbose
    else logging.WARNING
  )
  setup_logging(level=log_level, console=args.verbose or args.debug)

  print("nonoka-cli: use --server to start the OpenCode backend.", file=sys.stderr)
  return 0


if __name__ == "__main__":
  sys.exit(main())
