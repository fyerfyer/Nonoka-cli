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

# Downstream dependencies (litellm, tree-sitter, etc.) assume a valid cwd.
# If nonoka-cli is spawned from a directory that has been deleted, chdir to
# a safe fallback before any heavy imports run.
if hasattr(os, "getcwd"):
  try:
    os.getcwd()
  except FileNotFoundError:
    os.chdir(os.path.expanduser("~"))

import structlog
from dotenv import load_dotenv

from nonoka_cli.bridge.server import main as server_main
from nonoka_cli.commands import benchmark_cmd, config_cmd, doctor_cmd, eval_cmd, opencode_cmd, plugin_cmd, run_cmd
from nonoka_cli.utils.logging import setup_logging

logger = structlog.get_logger("nonoka_cli.cli")


def _load_env_files() -> None:
  """Load .env files so config env-var substitution works transparently.

  Priority (lowest to highest):
  1. ~/.config/nonoka/.env
  2. ./.env
  Existing environment variables always win.
  """
  global_env = Path.home() / ".config" / "nonoka" / ".env"
  local_env = Path.cwd() / ".env"
  for path in (global_env, local_env):
    if path.exists():
      load_dotenv(dotenv_path=path, override=False)
      logger.debug("loaded_env_file", path=str(path))


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

  subparsers = parser.add_subparsers(dest="command")
  config_cmd.add_subparser(subparsers)
  doctor_cmd.add_subparser(subparsers)
  opencode_cmd.add_subparser(subparsers)
  plugin_cmd.add_subparser(subparsers)
  run_cmd.add_subparser(subparsers)
  eval_cmd.add_subparser(subparsers)
  benchmark_cmd.add_subparser(subparsers)

  return parser


def main() -> int:
  """Main entry point.

  Returns:
    Exit code (0 for success, 1 for error).
  """
  parser = _build_parser()
  args = parser.parse_args()

  # Load .env files early so configuration and LLM providers see API keys.
  _load_env_files()

  if args.server:
    return server_main(config_path=args.config, model=args.model)

  if args.command and getattr(args, "func", None):
    log_level = (
      logging.DEBUG if args.debug
      else logging.INFO if args.verbose
      else logging.WARNING
    )
    setup_logging(level=log_level, console=args.verbose or args.debug)
    return args.func(args)

  # No subcommand given: launch the OpenCode TUI by default.
  log_level = (
    logging.DEBUG if args.debug
    else logging.INFO if args.verbose
    else logging.WARNING
  )
  setup_logging(level=log_level, console=args.verbose or args.debug)
  return run_cmd.launch_tui(args)


if __name__ == "__main__":
  sys.exit(main())
