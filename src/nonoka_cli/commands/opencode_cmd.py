"""OpenCode integration commands for nonoka-cli."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError

# Tool categories that nonoka-cli needs OpenCode to auto-approve when
# ``cli.auto_approve`` is enabled. These are OpenCode's native permission
# keys, not nonoka-cli's internal tool names.
_OPENCODE_AUTO_APPROVED_TOOLS = ["read", "bash", "edit", "write", "todowrite"]


def _build_opencode_permission(config: CLIConfig) -> dict[str, str]:
  """Build an OpenCode permission block from nonoka's CLI behavior config.

  When ``cli.auto_approve`` is true, allow the core coding tools so the
  agent can edit files and run commands without a HITL prompt. Otherwise
  require explicit approval for mutating operations.
  """
  auto_approve = getattr(config.cli, "auto_approve", False)
  action = "allow" if auto_approve else "ask"
  permission: dict[str, str] = {"*": "ask"}
  for tool in _OPENCODE_AUTO_APPROVED_TOOLS:
    permission[tool] = action
  return permission


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
  # Permission is regenerated from nonoka.yaml in cmd_init.
  "permission": {
    "*": "ask",
    "read": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "todowrite": "ask",
  },
  "agent": {
    "build": {
      "mode": "primary",
      # Permission is regenerated from nonoka.yaml in cmd_init.
      "permission": {
        "*": "ask",
        "read": "ask",
        "bash": "ask",
        "edit": "ask",
        "write": "ask",
        "todowrite": "ask",
      },
    }
  },
  # Disable OpenCode's native skill tool. nonoka-cli registers its own
  # skill__<skill>__<tool> and load_skill tools; leaving OpenCode's native
  # skill enabled would cause the model to receive conflicting instructions
  # (skill:<name> vs skill__<name>__<tool>).
  "tools": {
    "skill": False
  },
}

_OPENCODE_PROMPT_GUIDELINES = (
  "\n"
  "OpenCode-specific guidelines:\n"
  "- You are running inside OpenCode. Use only the tools provided by OpenCode.\n"
  "- Tool names available in this environment include bash, read, write, edit, and todowrite.\n"
  "- For multi-step tasks, keep the OpenCode TODO panel up to date using the todowrite tool.\n"
  "- Do not explore directories or read files unless the user explicitly requests it.\n"
  "- When writing or editing files, use absolute paths under the current working directory "
  "unless the user provides a different path.\n"
  "- Prefer reading a file before editing it when you need context.\n"
)


def _build_opencode_agent_prompt(config: CLIConfig) -> str:
  """Wrap the canonical nonoka system prompt for OpenCode's agent file.

  nonoka owns the system prompt (via ``system_prompt`` in nonoka.yaml). The
  OpenCode adapter places it in ``.opencode/agents/build.md`` and appends
  OpenCode-specific tool guidelines so the model behaves correctly inside
  OpenCode without leaking frontend details into nonoka's core prompt.
  """
  body = config.system_prompt.strip() or "You are a helpful coding assistant."
  permission = _build_opencode_permission(config)
  yaml_lines = "\n".join(
    f"  {json.dumps(k)}: {v}" if k == "*" else f"  {k}: {v}"
    for k, v in permission.items()
  )
  hitl_note = (
    "- Tool calls are auto-approved because nonoka.yaml has cli.auto_approve enabled.\n"
    if getattr(config.cli, "auto_approve", False)
    else "- Tool calls require user approval; keep changes focused and explain commands.\n"
  )
  return f"""---
permission:
{yaml_lines}
---

{body}{_OPENCODE_PROMPT_GUIDELINES}{hitl_note}"""


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
    config_path = str(Path(args.config).expanduser().resolve())
  else:
    config_path = str(ConfigLoader.DEFAULT_PATH)
  provider_block["options"]["configPath"] = config_path
  provider_block["options"]["serverCommand"] = [
    "bash",
    "-c",
    f"nonoka-cli --server --config {config_path} 2>/tmp/nonoka-server.log",
  ]

  merged.setdefault("provider", {})
  merged["provider"]["nonoka"] = provider_block

  # Reflect nonoka.yaml's cli.auto_approve in OpenCode's permission layer.
  permission_block = _build_opencode_permission(config)
  merged["permission"] = permission_block
  merged.setdefault("agent", {})
  merged["agent"].setdefault("build", {})
  merged["agent"]["build"]["permission"] = dict(permission_block)

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
        _build_opencode_agent_prompt(config),
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
