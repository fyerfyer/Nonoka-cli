"""Configuration management commands for nonoka-cli."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
from typing import Any

import structlog
import yaml

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIBehaviorConfig, CLIConfig, HITLConfigModel
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.commands.config")


_GLOBAL_ENV_PATH = Path.home() / ".config" / "nonoka" / ".env"


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


def _read_secret(prompt: str) -> str:
  """Read a secret from stdin, masking input when possible."""
  try:
    return getpass.getpass(f"{prompt}: ").strip()
  except (EOFError, KeyboardInterrupt):
    return ""
  except Exception:
    # Fallback for non-TTY environments where getpass may fail.
    value = _read_input(f"{prompt} (input will be visible)")
    if value:
      print("Warning: input was visible because this terminal does not support secure input.")
    return value


def _load_env_file(path: Path) -> dict[str, str]:
  """Parse a simple KEY=VALUE .env file, ignoring comments and blank lines."""
  values: dict[str, str] = {}
  if not path.exists():
    return values
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
      continue
    if "=" in line:
      key, value = line.split("=", 1)
      values[key.strip()] = value.strip()
  return values


def _write_env_file(path: Path, env_var: str, value: str) -> None:
  """Write or update a key in a .env file."""
  path.parent.mkdir(parents=True, exist_ok=True)
  values = _load_env_file(path)
  values[env_var] = value

  lines: list[str] = ["# nonoka API keys and environment overrides", ""]
  for key, val in values.items():
    if " " in val or "#" in val:
      val = f'"{val}"'
    lines.append(f"{key}={val}")
  lines.append("")
  path.write_text("\n".join(lines), encoding="utf-8")
  # Restrict permissions on the .env file.
  path.chmod(0o600)


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


_DEFAULT_DANGEROUS_TOOLS = [
  "write_file",
  "edit_file",
  "delete_file",
  "execute_command",
]

_DEFAULT_SYSTEM_PROMPT = """You are a helpful coding assistant. Be concise, direct, and helpful.

Only use tools when the user explicitly asks you to perform an operation such as running a command, reading a file, creating a file, or modifying a file. For direct questions, greetings, math problems, translations, and general explanations, answer directly without invoking tools."""


def _api_key_env_for_model(model: str) -> str:
  """Return a conventional API-key env var name for a model identifier."""
  lowered = model.lower()
  if "openai" in lowered or lowered.startswith("gpt-"):
    return "OPENAI_API_KEY"
  if "anthropic" in lowered or "claude" in lowered:
    return "ANTHROPIC_API_KEY"
  if "deepseek" in lowered:
    return "DEEPSEEK_API_KEY"
  if "openrouter" in lowered:
    return "OPENROUTER_API_KEY"
  if "gemini" in lowered or "google" in lowered:
    return "GOOGLE_API_KEY"
  return "OPENAI_API_KEY"


def _api_key_source_summary(env_var: str) -> str:
  """Return a short description of where the API key is currently sourced."""
  if os.getenv(env_var):
    return f"from environment (${env_var})"
  if _load_env_file(_GLOBAL_ENV_PATH).get(env_var):
    return f"from {_GLOBAL_ENV_PATH}"
  return ""


def _collect_api_key(model: str) -> tuple[str, str, str]:
  """Collect API key info and optionally persist it.

  Returns:
    (env_var_name, api_key_for_config, summary_for_user)
  """
  env_var = _api_key_env_for_model(model)
  existing = os.getenv(env_var) or _load_env_file(_GLOBAL_ENV_PATH).get(env_var)
  if existing:
    print(f"Found {env_var} already set.")
    return env_var, "", f"using existing ${env_var}"

  key_input = _read_secret(f"{env_var} value (press Enter to skip)")
  if not key_input:
    return env_var, "", "no key provided"

  print("")
  print("Where would you like to save the API key?")
  print("  [d] ~/.config/nonoka/.env (recommended, auto-loaded, file permission 600)")
  print("  [c] directly in config.yaml (not recommended)")
  print("  [s] skip saving, set it manually later")
  choice = _read_input("Choice", "d").lower().strip()

  if choice in ("c", "config"):
    print("Warning: the API key will be stored in plain text in config.yaml.")
    return env_var, key_input, "stored in config.yaml"

  if choice in ("s", "skip"):
    print(f"Remember to set the key later: export {env_var}=<your-key>")
    return env_var, "", "not saved"

  # Default: save to .env
  _write_env_file(_GLOBAL_ENV_PATH, env_var, key_input)
  # Make it available for the rest of this process too.
  os.environ[env_var] = key_input
  print(f"Saved {env_var} to {_GLOBAL_ENV_PATH}")
  return env_var, "", f"saved to {_GLOBAL_ENV_PATH}"


def cmd_init(args: argparse.Namespace) -> int:
  """Create an initial nonoka config, interactively or with --yes defaults."""
  path = Path(args.config) if args.config else ConfigLoader.DEFAULT_PATH

  print(f"Creating nonoka configuration at: {path}")

  if getattr(args, "yes", False):
    model = getattr(args, "model", None) or "deepseek-chat"
    auto_approve = getattr(args, "auto_approve", False)
    env_var = _api_key_env_for_model(model)
    key_summary = _api_key_source_summary(env_var) or "not configured"

    config = CLIConfig(
      model=model,
      system_prompt=_DEFAULT_SYSTEM_PROMPT,
      api_key="",
      cli=CLIBehaviorConfig(auto_approve=auto_approve),
      hitl=HITLConfigModel(
        policy="auto" if auto_approve else "interactive",
        dangerous_tools=[] if auto_approve else _DEFAULT_DANGEROUS_TOOLS,
      ),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ConfigLoader.save(config, path)

    print("")
    print(f"Configuration saved to {path}")
    print(f"Model: {model}")
    print(f"API key ({env_var}): {key_summary}")
    if not os.getenv(env_var) and not _load_env_file(_GLOBAL_ENV_PATH).get(env_var):
      print(
        f"Set your API key with: nonoka-cli config init (interactive) "
        f"or export {env_var}=<your-key>"
      )
    return 0

  print("")
  print(
    "Examples: deepseek-chat, openai/gpt-4o, "
    "anthropic/claude-sonnet-4-20250514, ollama/llama3.3"
  )

  model = _read_input("Model identifier", "deepseek-chat")
  env_var, api_key_value, key_summary = _collect_api_key(model)

  system_prompt = _read_input(
    "Optional system prompt",
    _DEFAULT_SYSTEM_PROMPT,
  )

  auto_approve = _confirm("Auto-approve all tool calls? (skips HITL)", default=False)

  config = CLIConfig(
    model=model,
    system_prompt=system_prompt,
    api_key=api_key_value,
    cli=CLIBehaviorConfig(auto_approve=auto_approve),
    hitl=HITLConfigModel(
      policy="auto" if auto_approve else "interactive",
      dangerous_tools=[] if auto_approve else _DEFAULT_DANGEROUS_TOOLS,
    ),
  )

  path.parent.mkdir(parents=True, exist_ok=True)
  ConfigLoader.save(config, path)

  print("")
  print(f"Configuration saved to {path}")
  print(f"API key ({env_var}): {key_summary}")
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
  init_parser.add_argument(
    "--yes", "-y",
    action="store_true",
    help="Non-interactive mode: create config with defaults",
  )
  init_parser.add_argument(
    "--model",
    default="deepseek-chat",
    help="Default model to write when using --yes",
  )
  init_parser.add_argument(
    "--auto-approve",
    action="store_true",
    help="Enable auto-approve when using --yes",
  )
  init_parser.set_defaults(func=cmd_init)

  set_parser = config_subparsers.add_parser("set", help="Set a config value")
  _add_config_arg(set_parser)
  set_parser.add_argument("key", help="Dotted key path, e.g. model or cli.theme")
  set_parser.add_argument("value", help="Value to store")
  set_parser.set_defaults(func=cmd_set)

  show_parser = config_subparsers.add_parser("show", help="Show current config")
  _add_config_arg(show_parser)
  show_parser.set_defaults(func=cmd_show)
