"""Launch the OpenCode TUI from nonoka-cli."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from nonoka_cli.commands.opencode_cmd import cmd_init
from nonoka_cli.commands.doctor_cmd import (
    _provider_version_from_project,
    check_opencode_config,
    check_provider,
)
from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.safety import PROCESS_SANDBOX_ENV, SrtSandbox


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

    print(f"No opencode.json found in {cwd}. Running 'nonoka init'...", file=sys.stderr)

    init_args = argparse.Namespace(
        config=getattr(args, "config", None),
        cwd=str(cwd),
        global_=False,
        yes=True,
        command="opencode",
        opencode_command="init",
    )
    return cmd_init(init_args)


def _referenced_config_path(cwd: Path) -> Path | None:
    """Return the config path selected by the project's provider block."""
    try:
        data = json.loads((cwd / "opencode.json").read_text(encoding="utf-8"))
        value = data["provider"]["nonoka"]["options"]["configPath"]
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (cwd / path).resolve()
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _run_preflight(args: argparse.Namespace, cwd: Path) -> tuple[Path, Any] | None:
    """Validate the executable OpenCode/Nonoka project contract."""
    config_result = check_opencode_config(cwd)
    provider_result = check_provider(cwd, require_project=True)
    failures = [result for result in (config_result, provider_result) if result.status == "error"]
    for result in failures:
        print(f"Error: {result.message}", file=sys.stderr)
        if result.remedy:
            print(f"  Try: {result.remedy}", file=sys.stderr)
    if failures:
        print(f"  Diagnose: nonoka doctor --cwd {cwd}", file=sys.stderr)
        return None

    referenced = _referenced_config_path(cwd)
    if referenced is None:
        print("Error: OpenCode config does not identify the active Nonoka config.", file=sys.stderr)
        return None

    explicit = getattr(args, "config", None)
    if explicit is not None:
        try:
            requested = ConfigLoader.find_config_file(explicit, search_dir=cwd)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        if requested != referenced:
            print("Error: --config does not match the config recorded in opencode.json.", file=sys.stderr)
            print(f"  Requested: {requested}", file=sys.stderr)
            print(f"  OpenCode:  {referenced}", file=sys.stderr)
            print(f"  Try: nonoka init --cwd {cwd} --config {requested}", file=sys.stderr)
            return None

    try:
        config = ConfigLoader.load(referenced)
    except Exception as exc:
        print(f"Error: failed to load {referenced}: {exc}", file=sys.stderr)
        return None
    return referenced, config


def _repo_state(cwd: Path) -> str:
    """Return a bounded, human-readable Git state for the readiness banner."""
    if not (cwd / ".git").exists():
        return "not a Git repository"
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return "unknown"
        count = len([line for line in result.stdout.splitlines() if line.strip()])
        return "clean" if count == 0 else f"dirty, {count} changed paths"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _print_readiness(cwd: Path, config_path: Path, config: Any) -> None:
    provider_version = _provider_version_from_project(cwd) or "unknown"
    capability_parts = [
        f"{len(config.mcp_servers)} MCP",
        f"{len(config.skills)} skills",
        f"{len(config.tool_paths)} custom tool paths",
    ]
    print("Nonoka readiness", file=sys.stderr)
    print(f"  Project       {cwd}", file=sys.stderr)
    print(f"  Config        {config_path}", file=sys.stderr)
    print(f"  Model         {config.model} via nonoka/default", file=sys.stderr)
    print(f"  Provider      {provider_version} ready", file=sys.stderr)
    print(f"  Capabilities  {' · '.join(capability_parts)}", file=sys.stderr)
    print(f"  Repo          {_repo_state(cwd)}", file=sys.stderr)
    repo_map_state = "enabled; backend resolves at runtime" if config.repo_map.enabled else "disabled"
    print(f"  Repo map      {repo_map_state}", file=sys.stderr)


def launch_tui(args: argparse.Namespace) -> int:
    """Launch the OpenCode TUI in the requested directory.

    If ``args.message`` is provided, run OpenCode in one-shot mode with
    ``opencode run --auto <message>``. Otherwise start the interactive TUI.
    """
    cwd = Path(getattr(args, "cwd", ".")).expanduser().resolve()

    if not cwd.exists():
        print(f"Error: working directory does not exist: {cwd}", file=sys.stderr)
        return 1
    if not cwd.is_dir():
        print(f"Error: working directory is not a directory: {cwd}", file=sys.stderr)
        return 1

    if not _has_opencode():
        print("Error: opencode is not installed.", file=sys.stderr)
        print("Install it with: npm install -g opencode", file=sys.stderr)
        return 1

    print("Preparing Nonoka: resolving project configuration...", file=sys.stderr)
    ret = _ensure_opencode_config(args, cwd)
    if ret != 0:
        return ret

    print("Preparing Nonoka: checking provider and bridge command...", file=sys.stderr)
    preflight = _run_preflight(args, cwd)
    if preflight is None:
        return 1
    config_path, config = preflight
    _print_readiness(cwd, config_path, config)
    print("Preparing Nonoka: ready; starting OpenCode...", file=sys.stderr)

    message = getattr(args, "message", None)
    if message:
        cmd = ["opencode", "run", "--auto", message]
    else:
        cmd = ["opencode", str(cwd)]

    # OpenCode-native bash/edit/write are descendants of this process, so the
    # only reliable boundary is wrapping the entire TUI process tree.
    settings = None
    if config.safety.enabled and config.safety.sandbox in {"auto", "srt"}:
        srt = SrtSandbox(config.safety.allowed_domains)
        executable = srt.executable()
        if not executable:
            if config.safety.required:
                print(
                    "Error: the required OpenCode process-tree sandbox needs SRT, "
                    "but `srt` is unavailable.",
                    file=sys.stderr,
                )
                print(
                    "Install it with `npm install -g @anthropic-ai/sandbox-runtime`, "
                    "then run `nonoka doctor --check-sandbox`.",
                    file=sys.stderr,
                )
                return 1
        else:
            settings = srt.settings(cwd)
            cmd = [executable, "--settings", str(settings), *cmd]

    try:
        launch_env = os.environ.copy()
        # OpenCode also exposes an environment-level updater guard.  Set it
        # for the whole TUI process tree so a launch cannot race an upgrade,
        # even if OpenCode reads update policy before the project config.
        launch_env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        if settings is not None:
            # The bridge and its custom tools inherit the outer SRT boundary.
            # Mark ownership so they do not try to bootstrap a nested SRT.
            launch_env[PROCESS_SANDBOX_ENV] = "srt"
        result = subprocess.run(cmd, cwd=cwd, env=launch_env)
        return result.returncode
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError:
        print("Error: opencode disappeared during launch.", file=sys.stderr)
        return 1
    finally:
        if settings is not None:
            settings.unlink(missing_ok=True)


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
