"""CLI entry point for nonoka-cli.

Handles argument parsing, configuration loading, and REPL startup.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import structlog

from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.shell.repl import REPL
from nonoka_cli.ui.renderer import Renderer
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
  return parser


async def _run_repl(args: argparse.Namespace) -> int:
  """Initialize and run the REPL.

  Args:
    args: Parsed command-line arguments.

  Returns:
    Exit code (0 for success).
  """
  orchestrator = Orchestrator()
  renderer = Renderer()
  repl = REPL(orchestrator, renderer)

  # Handle Ctrl+C by interrupting the current execution
  loop = asyncio.get_event_loop()

  def _sigint_handler():
    asyncio.create_task(repl.interrupt())

  if sys.platform != "win32":
    loop.add_signal_handler(signal.SIGINT, _sigint_handler)

  try:
    await orchestrator.initialize(config_path=args.config)
  except Exception as exc:
    print(f"Failed to initialize: {exc}", file=sys.stderr)
    logger.error("initialization_failed", error=str(exc))
    return 1

  # Apply model override if provided
  if args.model:
    orchestrator.config.model = args.model
    orchestrator._agent_factory.rebuild()
    logger.info("model_overridden", model=args.model)

  print(f"nonoka-cli initialized. Model: {orchestrator.config.model}")
  print("Type /help for commands, /exit to quit.")
  print()

  try:
    await repl.run()
  finally:
    await orchestrator.shutdown()
    if sys.platform != "win32":
      loop.remove_signal_handler(signal.SIGINT)

  return 0


def main() -> int:
  """Main entry point.

  Returns:
    Exit code (0 for success, 1 for error).
  """
  parser = _build_parser()
  args = parser.parse_args()

  # Setup logging
  log_level = (
    logging.DEBUG if args.debug
    else logging.INFO if args.verbose
    else logging.WARNING
  )
  setup_logging(level=log_level, console=args.verbose or args.debug)

  try:
    return asyncio.run(_run_repl(args))
  except KeyboardInterrupt:
    print("\nInterrupted.")
    return 130
  except Exception as exc:
    logger.error("fatal_error", error=str(exc))
    print(f"Fatal error: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  sys.exit(main())
