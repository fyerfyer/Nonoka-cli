"""Thin public CLI facade for the framework-owned evaluation engine."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def cmd_eval(args: argparse.Namespace) -> int:
  """Delegate ``nonoka-cli eval`` arguments to ``nonoka.ext.eval``.

  Keeping parsing and execution in nonoka-agent prevents the CLI/OpenCode
  integration package from becoming a second source of benchmark semantics.
  """
  try:
    from nonoka.ext.eval.__main__ import main as eval_main
  except ImportError:
    print(
      "Error: evaluation support is unavailable. Install a nonoka version with "
      "the eval module included.",
      file=sys.stderr,
    )
    return 10
  forwarded = (
    ["--help"]
    if getattr(args, "eval_help", False)
    else list(getattr(args, "eval_args", []))
  )
  return int(eval_main(forwarded))


def add_subparser(subparsers: Any) -> None:
  parser = subparsers.add_parser(
    "eval",
    add_help=False,
    help="Run framework-owned agent evaluations (paired with a direct baseline)",
    description=(
      "Evaluation is implemented by nonoka-agent. Examples: "
      "nonoka-cli eval list; nonoka-cli eval run --dataset humaneval "
      "--model deepseek/deepseek-v4-pro"
    ),
  )
  parser.add_argument(
    "-h", "--help",
    dest="eval_help",
    action="store_true",
    help="Show framework evaluation command help.",
  )
  parser.add_argument(
    "eval_args",
    nargs=argparse.REMAINDER,
    help="Arguments passed directly to the framework evaluation engine.",
  )
  parser.set_defaults(func=cmd_eval)
