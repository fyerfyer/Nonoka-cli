"""Launch the OpenCode TUI from nonoka-cli."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from nonoka_cli.commands.opencode_cmd import cmd_init


def _has_opencode() -> bool:
    """Return True if the opencode binary is available on PATH."""
    return shutil.which("opencode") is not None


def _has_opencode_config(cwd: Path) -> bool:
    """Return True if the directory already has an opencode.json."""
    return (cwd / "opencode.json").exists()


def _ensure_opencode_config(args: argparse.Namespace, cwd: Path) -> int:
    """Initialize opencode.json if it is missing.

    Returns:
        0 if the config exists or was created successfully, otherwise an error code.
    """
    if _has_opencode_config(cwd):
        return 0

    print(f"No opencode.json found in {cwd}. Running 'nonoka-cli opencode init'...", file=sys.stderr)

    init_args = argparse.Namespace(
        config=getattr(args, "config", None),
        cwd=str(cwd),
        global_=False,
        yes=True,
        command="opencode",
        opencode_command="init",
    )
    return cmd_init(init_args)


def launch_tui(args: argparse.Namespace) -> int:
    """Launch the OpenCode TUI in the requested directory.

    If ``args.message`` is provided, run OpenCode in one-shot mode with
    ``opencode run --auto <message>``. Otherwise start the interactive TUI.
    """
    cwd = Path(getattr(args, "cwd", ".")).expanduser().resolve()

    if not _has_opencode():
        print("Error: opencode is not installed.", file=sys.stderr)
        print("Install it with: npm install -g opencode", file=sys.stderr)
        return 1

    ret = _ensure_opencode_config(args, cwd)
    if ret != 0:
        return ret

    message = getattr(args, "message", None)
    if message:
        cmd = ["opencode", "run", "--auto", message]
    else:
        cmd = ["opencode", str(cwd)]

    try:
        result = subprocess.run(cmd, cwd=cwd)
        return result.returncode
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError:
        print("Error: opencode disappeared during launch.", file=sys.stderr)
        return 1


def cmd_run(args: argparse.Namespace) -> int:
    """Entry point for the ``nonoka-cli run`` subcommand."""
    return launch_tui(args)


def add_subparser(subparsers: Any) -> None:
    """Register the ``run`` subcommand."""
    run_parser = subparsers.add_parser(
        "run",
        help="Launch the OpenCode TUI (default when no subcommand is given)",
    )
    run_parser.add_argument(
        "--config",
        dest="config",
        type=Path,
        default=None,
        help="Path to the nonoka config file (default: ~/.config/nonoka/config.yaml)",
    )
    run_parser.add_argument(
        "--cwd",
        dest="cwd",
        default=".",
        help="Directory to start OpenCode in (default: current directory)",
    )
    run_parser.add_argument(
        "--message", "-m",
        dest="message",
        default=None,
        help="Run a single message and exit instead of starting the interactive TUI",
    )
    run_parser.set_defaults(func=cmd_run)
