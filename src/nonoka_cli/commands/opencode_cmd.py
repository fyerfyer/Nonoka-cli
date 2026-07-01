"""OpenCode integration commands for nonoka-cli."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError

_DEFAULT_OPENCODE_CONFIG = {
  "$schema": "https://opencode.ai/config.json",
  "model": "nonoka/default",
  "provider": {
    "nonoka": {
      "npm": "nonoka-opencode-provider",
      "name": "Nonoka",
      "options": {
        "serverCommand": ["nonoka-cli", "--server"],
        "cwd": ".",
      },
      "models": {
        "default": {"name": "Nonoka Default"}
      }
    }
  },
  "permission": {
    "edit": "ask",
    "bash": "ask"
  }
}


def _load_config(args: argparse.Namespace) -> CLIConfig:
  """Load nonoka config or return defaults."""
  path = getattr(args, "config", None)
  try:
    return ConfigLoader.load(path)
  except ConfigError:
    return CLIConfig()


def _global_opencode_path() -> Path:
  """Return the user-level OpenCode config path."""
  return Path.home() / ".config" / "opencode" / "opencode.json"


def cmd_init(args: argparse.Namespace) -> int:
  """Generate or merge an opencode.json for the current project."""
  config = _load_config(args)

  target: Path
  if args.global_:
    target = _global_opencode_path()
  else:
    target = Path(args.cwd) / "opencode.json"

  print(f"Writing OpenCode config to: {target}")

  existing: dict[str, Any] = {}
  if target.exists():
    try:
      existing = json.loads(target.read_text(encoding="utf-8"))
      print("Existing opencode.json found; merging provider block.")
    except json.JSONDecodeError as exc:
      print(f"Failed to parse existing {target}: {exc}")
      return 1

  merged = {**_DEFAULT_OPENCODE_CONFIG, **existing}

  # Inject/update the nonoka provider block.
  provider_block = dict(_DEFAULT_OPENCODE_CONFIG["provider"]["nonoka"])
  if config.model:
    provider_block["models"] = {
      "default": {"name": f"Nonoka {config.model}"}
    }
  if args.config:
    provider_block["options"]["configPath"] = str(Path(args.config).expanduser().resolve())
  else:
    provider_block["options"]["configPath"] = str(ConfigLoader.DEFAULT_PATH)

  merged.setdefault("provider", {})
  merged["provider"]["nonoka"] = provider_block

  # Only set the top-level model if it is not already configured.
  if not existing.get("model"):
    merged["model"] = "nonoka/default"

  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_text(
    json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
  )

  print(f"OpenCode config saved to {target}")
  print("Install the provider with: npm install -g nonoka-opencode-provider")
  print("Then run: opencode")
  return 0


def _add_config_arg(parser: Any) -> None:
  parser.add_argument(
    "--config",
    dest="config",
    help="Path to the nonoka config file (default: ~/.config/nonoka/config.yaml)",
  )


def add_subparser(subparsers: Any) -> None:
  """Register the ``opencode`` subcommand and its children."""
  opencode_parser = subparsers.add_parser("opencode", help="Manage OpenCode integration")
  _add_config_arg(opencode_parser)
  opencode_parser.add_argument(
    "--cwd",
    dest="cwd",
    default=".",
    help="Directory to write opencode.json into (default: current directory)",
  )
  opencode_parser.add_argument(
    "--global",
    dest="global_",
    action="store_true",
    help="Write to ~/.config/opencode/opencode.json instead of ./opencode.json",
  )

  opencode_subparsers = opencode_parser.add_subparsers(dest="opencode_command", required=True)
  init_parser = opencode_subparsers.add_parser("init", help="Generate or merge opencode.json")
  _add_config_arg(init_parser)
  init_parser.add_argument(
    "--cwd",
    dest="cwd",
    default=".",
    help="Directory to write opencode.json into (default: current directory)",
  )
  init_parser.add_argument(
    "--global",
    dest="global_",
    action="store_true",
    help="Write to ~/.config/opencode/opencode.json instead of ./opencode.json",
  )
  init_parser.set_defaults(func=cmd_init)
