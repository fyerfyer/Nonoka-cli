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
        "serverCommand": ["bash", "-c", "nonoka-cli --server 2>/tmp/nonoka-server.log"],
        "cwd": ".",
      },
      "models": {
        "default": {"name": "Nonoka Default"}
      }
    }
  },
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
  },
  "agent": {
    "build": {
      "mode": "primary",
      "permission": {
        "*": "ask",
        "bash": "ask",
        "edit": "ask",
        "write": "ask",
      },
    }
  },
}

_OPENCODE_PROMPT_GUIDELINES = """

OpenCode-specific guidelines:
- You are running inside OpenCode. Use only the tools provided by OpenCode.
- Tool names available in this environment include bash, read, write, and edit.
- Do not explore directories or read files unless the user explicitly requests it.
- When writing or editing files, use absolute paths under the current working directory unless the user provides a different path.
- Prefer reading a file before editing it when you need context.
- Every tool call requires user approval in this environment, so choose the simplest and most direct way to satisfy the request.
"""


def _build_opencode_agent_prompt(system_prompt: str) -> str:
  """Wrap the canonical nonoka system prompt for OpenCode's agent file.

  nonoka owns the system prompt (via ``system_prompt`` in nonoka.yaml). The
  OpenCode adapter places it in ``.opencode/agents/build.md`` and appends
  OpenCode-specific tool guidelines so the model behaves correctly inside
  OpenCode without leaking frontend details into nonoka's core prompt.
  """
  body = system_prompt.strip() or "You are a helpful coding assistant."
  return f"""---
permission:
  "*": ask
  bash: ask
  edit: ask
  write: ask
---

{body}{_OPENCODE_PROMPT_GUIDELINES}
"""


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

  # Create the OpenCode agent prompt file for project-level installs.
  # nonoka owns the system prompt; this is the OpenCode adapter that places it
  # where OpenCode expects its primary agent prompt.
  if not args.global_:
    agent_dir = Path(args.cwd) / ".opencode" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_dir / "build.md"
    if not agent_file.exists():
      agent_file.write_text(
        _build_opencode_agent_prompt(config.system_prompt),
        encoding="utf-8",
      )
      print(f"Agent prompt saved to {agent_file}")
    else:
      print(f"Agent prompt already exists at {agent_file}; not overwriting.")

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
    "--yes", "-y",
    action="store_true",
    help="Non-interactive mode (default; kept for script compatibility)",
  )
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
