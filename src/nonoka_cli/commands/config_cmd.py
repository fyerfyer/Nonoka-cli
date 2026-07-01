"""Configuration management commands for nonoka-cli."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import structlog
import yaml

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIBehaviorConfig, CLIConfig
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.commands.config")


def _load_manager(args: argparse.Namespace) -> ConfigManager:
  """Load or create a ConfigManager from the requested path."""
  path = getattr(args, "config", None)
  try:
    return ConfigManager.load(path)
  except ConfigError as exc:
    if "not found" in str(exc).lower():
      cfg_path = Path(path) if path else ConfigLoader.DEFAULT_PATH
      return ConfigManager(CLIConfig(), config_path=cfg_path)
    raise


def _read_input(prompt: str, default: str = "") -> str:
  """Read a line from stdin, returning *default* on empty input."""
  try:
    value = input(f"{prompt}: ").strip()
  except (EOFError, KeyboardInterrupt):
    value = ""
  return value if value else default


def _confirm(prompt: str, default: bool = False) -> bool:
  """Ask a yes/no question."""
  suffix = "(Y/n)" if default else "(y/N)"
  answer = _read_input(f"{prompt} {suffix}").lower()
  if not answer:
    return default
  return answer in ("y", "yes")


def _set_dotted(data: dict[str, Any], key: str, value: Any) -> None:
  """Set a possibly dotted key in a nested dict, creating parents as needed."""
  parts = key.split(".")
  target = data
  for part in parts[:-1]:
    if part not in target or not isinstance(target[part], dict):
      target[part] = {}
    target = target[part]
  target[parts[-1]] = value


def _coerce_value(raw: str) -> Any:
  """Coerce a CLI string value into bool/int/list/string."""
  lowered = raw.lower()
  if lowered in ("true", "false"):
    return lowered == "true"
  try:
    return int(raw)
  except ValueError:
    pass
  try:
    return float(raw)
  except ValueError:
    pass
  if raw.startswith("[") and raw.endswith("]"):
    try:
      return json.loads(raw)
    except json.JSONDecodeError:
      pass
  return raw


def cmd_init(args: argparse.Namespace) -> int:
  """Interactive wizard to create an initial nonoka config."""
  path = Path(args.config) if args.config else ConfigLoader.DEFAULT_PATH

  print(f"Creating nonoka configuration at: {path}")
  print("")
  print(
    "Examples: deepseek-chat, openai/gpt-4o, "
    "anthropic/claude-sonnet-4-20250514, ollama/llama3.3"
  )

  model = _read_input("Model identifier", "deepseek-chat")
  env_var = _read_input(
    "Environment variable for the API key (optional, e.g. DEEPSEEK_API_KEY)",
    "",
  )

  api_key_display = ""
  if env_var:
    existing = os.getenv(env_var, "")
    if existing:
      print(f"Found {env_var} in environment.")
      api_key_display = "${" + env_var + "}"
    else:
      key_input = _read_input(f"{env_var} value (press Enter to skip)", "")
      if key_input:
        prompt = "Save the API key directly in the config file? (Not recommended)"
        if _confirm(prompt, default=False):
          api_key_display = key_input
        else:
          api_key_display = "${" + env_var + "}"
          print(f"Please export {env_var} in your environment before running nonoka.")

  system_prompt = _read_input(
    "Optional system prompt",
    "You are a helpful coding assistant. Be concise and helpful.",
  )

  auto_approve = _confirm("Auto-approve all tool calls? (skips HITL)", default=False)

  config = CLIConfig(
    model=model,
    system_prompt=system_prompt,
    cli=CLIBehaviorConfig(auto_approve=auto_approve),
  )

  path.parent.mkdir(parents=True, exist_ok=True)
  ConfigLoader.save(config, path)

  print("")
  print(f"Configuration saved to {path}")
  if env_var and not os.getenv(env_var) and api_key_display.startswith("${"):
    print(f"Remember to export your API key: export {env_var}=<your-key>")
  return 0


def cmd_set(args: argparse.Namespace) -> int:
  """Set a config key and persist it."""
  manager = _load_manager(args)
  key: str = args.key
  raw_value: str = args.value

  data = manager.config.model_dump(mode="json")
  value = _coerce_value(raw_value)
  _set_dotted(data, key, value)

  try:
    new_config = CLIConfig.model_validate(data)
  except Exception as exc:
    print(f"Invalid value for '{key}': {exc}")
    return 1

  manager.save(new_config)
  print(f"Set {key} = {value!r}")
  return 0


def cmd_show(args: argparse.Namespace) -> int:
  """Print the current configuration."""
  manager = _load_manager(args)
  path = manager.config_path or ConfigLoader.DEFAULT_PATH
  print(f"Config path: {path}")
  print("")
  print(yaml.safe_dump(
    manager.config.model_dump(mode="json"),
    sort_keys=False,
    allow_unicode=True,
  ))
  return 0


def _add_config_arg(parser: Any) -> None:
  parser.add_argument(
    "--config",
    dest="config",
    help="Path to the nonoka config file (default: ~/.config/nonoka/config.yaml)",
  )


def add_subparser(subparsers: Any) -> None:
  """Register the ``config`` subcommand and its children."""
  config_parser = subparsers.add_parser("config", help="Manage nonoka configuration")
  _add_config_arg(config_parser)
  config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

  init_parser = config_subparsers.add_parser("init", help="Create an initial config file")
  _add_config_arg(init_parser)
  init_parser.set_defaults(func=cmd_init)

  set_parser = config_subparsers.add_parser("set", help="Set a config value")
  _add_config_arg(set_parser)
  set_parser.add_argument("key", help="Dotted key path, e.g. model or cli.theme")
  set_parser.add_argument("value", help="Value to store")
  set_parser.set_defaults(func=cmd_set)

  show_parser = config_subparsers.add_parser("show", help="Show current config")
  _add_config_arg(show_parser)
  show_parser.set_defaults(func=cmd_show)
