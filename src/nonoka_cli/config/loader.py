"""Configuration loading with env-var substitution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError, ConfigNotFoundError

logger = structlog.get_logger("nonoka_cli.config")

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-?(?P<default>[^}]*))?\}")


def _format_pydantic_errors(exc: ValidationError) -> str:
  """Format Pydantic ValidationError into a human-readable message.

  Returns a multi-line string with error locations, messages, and suggestions.
  """
  lines: list[str] = []
  for error in exc.errors():
    loc = ".".join(str(part) for part in error.get("loc", []))
    msg = error.get("msg", "Invalid value")
    input_value = error.get("input")
    error_type = error.get("type", "")

    location = loc if loc else "<root>"
    lines.append(f"  • [{location}] {msg}")

    if input_value is not None:
      lines.append(f"    Got value: {input_value!r}")

    suggestion = _suggest_fix(error_type, location)
    if suggestion:
      lines.append(f"    Suggestion: {suggestion}")

  return "\n".join(lines)


def _suggest_fix(error_type: str, location: str) -> str:
  """Return a human-readable fix suggestion for common Pydantic errors."""
  if error_type in ("int_parsing", "int_type"):
    return "Use an integer value (e.g., 1000) instead of a string."
  if error_type in ("float_parsing", "float_type"):
    return "Use a numeric value (e.g., 1.5) instead of a string."
  if error_type in ("bool_parsing", "bool_type"):
    return "Use true/false (without quotes) for boolean values."
  if error_type == "missing":
    return f"Add the missing '{location}' field to your config file."
  if error_type == "extra_forbidden":
    return f"Remove the unexpected field '{location}' or check for typos."
  if "enum" in error_type:
    return "Use one of the allowed values for this field."
  if error_type == "string_type":
    return "Use a plain string value (without quotes if already a string)."
  if error_type == "list_type":
    return "Use a YAML list (e.g., [item1, item2] or one item per line starting with '-')."
  if error_type == "dict_type":
    return "Use a YAML object with named keys for this field."
  if error_type == "value_error":
    return "Check that the value meets the field requirements."
  return ""


def _substitute_env_vars(value: Any) -> Any:
  """Recursively substitute ``${VAR}`` and ``${VAR:-default}`` in strings."""
  if isinstance(value, str):

    def replacer(match: re.Match[str]) -> str:
      var_name = match.group("name")
      default = match.group("default")
      result = os.getenv(var_name)
      if result is None:
        if default is not None:
          return default
        raise ConfigError(f"Environment variable '{var_name}' is not set and no default provided")
      return result

    return _ENV_PATTERN.sub(replacer, value)
  if isinstance(value, dict):
    return {k: _substitute_env_vars(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_substitute_env_vars(item) for item in value]
  return value


class ConfigLoader:
  """Loads and validates CLI configuration from YAML files.

  Supports a main ``config.yaml`` plus an optional ``mcp_servers.yaml``
  side-car file. The side-car file is merged into the main config under
  the ``mcp_servers`` key, with side-car entries taking precedence over
  main-config entries of the same name.
  """

  DEFAULT_PATH = Path.home() / ".config" / "nonoka" / "config.yaml"
  MCP_SERVERS_PATH = Path.home() / ".config" / "nonoka" / "mcp_servers.yaml"

  @classmethod
  def fallback_path(cls) -> Path:
    """Return the runtime fallback config path (./nonoka.yaml)."""
    return Path.cwd() / "nonoka.yaml"

  @classmethod
  def find_config_file(
    cls,
    explicit_path: Path | str | None = None,
  ) -> Path:
    """Search for a configuration file in priority order.

    Priority: explicit_path > ~/.config/nonoka/config.yaml > ./nonoka.yaml
    """
    if explicit_path is not None:
      path = Path(explicit_path)
      if path.exists():
        return path
      raise ConfigNotFoundError(f"Explicit config file not found: {path}")

    if cls.DEFAULT_PATH.exists():
      return cls.DEFAULT_PATH

    fallback = cls.fallback_path()
    if fallback.exists():
      return fallback

    raise ConfigNotFoundError(f"No config file found. Searched: {cls.DEFAULT_PATH}, {fallback}")

  @classmethod
  def _load_yaml(cls, path: Path) -> dict[str, Any]:
    """Load a YAML file and return a dict (empty if file missing)."""
    import yaml

    if not path.exists():
      return {}

    try:
      raw = path.read_text(encoding="utf-8")
      data = yaml.safe_load(raw)
    except Exception as exc:
      raise ConfigError(f"Failed to parse YAML from {path}: {exc}") from exc

    if data is None:
      return {}
    if not isinstance(data, dict):
      raise ConfigError(
        f"Config file must contain a top-level object, got {type(data).__name__}: {path}"
      )
    return data

  @classmethod
  def load(
    cls,
    path: Path | str | None = None,
  ) -> CLIConfig:
    """Load configuration from a YAML file.

    Args:
      path: Explicit config file path. If None, searches default locations.

    Returns:
      Validated CLIConfig instance.

    Raises:
      ConfigNotFoundError: If no config file is found.
      ConfigError: If parsing or validation fails.
    """
    config_path = cls.find_config_file(path)
    logger.info("loading_config", path=str(config_path))

    try:
      import yaml  # noqa: F401
    except ImportError as exc:
      raise ConfigError("PyYAML is required. Install: pip install pyyaml") from exc

    data = cls._load_yaml(config_path)
    agents_data = data.get("agents")
    if isinstance(agents_data, dict) and "planner" in agents_data:
      logger.warning(
        "deprecated_agents_planner_ignored",
        path=str(config_path),
        replacement=".nonoka/plugin.json agents[]",
      )

    # Merge optional side-car MCP servers file.
    mcp_data = cls._load_yaml(cls.MCP_SERVERS_PATH)
    if "mcp_servers" in mcp_data:
      data.setdefault("mcp_servers", {})
      data["mcp_servers"].update(mcp_data["mcp_servers"])
      logger.info("merged_mcp_servers", path=str(cls.MCP_SERVERS_PATH))

    # Substitute env vars before validation
    try:
      data = _substitute_env_vars(data)
    except ConfigError:
      raise
    except Exception as exc:
      raise ConfigError(f"Environment variable substitution failed: {exc}") from exc

    try:
      config = CLIConfig.model_validate(data)
    except ValidationError as exc:
      formatted = _format_pydantic_errors(exc)
      raise ConfigError(f"Config validation failed for {config_path}:\n{formatted}") from exc
    except Exception as exc:
      raise ConfigError(f"Config validation failed: {exc}") from exc

    logger.info("config_loaded", model=config.model)
    return config

  @classmethod
  def save(cls, config: CLIConfig, path: Path | str | None = None) -> Path:
    """Save a CLIConfig to a YAML file.

    Args:
      config: The configuration to persist.
      path: Destination path. Defaults to ``~/.config/nonoka/config.yaml``.

    Returns:
      Path to the written file.
    """
    import yaml

    target = Path(path) if path is not None else cls.DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", exclude_none=True)
    # Keep the file tidy by dropping empty default containers.
    if not data.get("mcp_servers"):
      data.pop("mcp_servers", None)
    if not data.get("tool_paths"):
      data.pop("tool_paths", None)
    if not data.get("skills"):
      data.pop("skills", None)

    target.write_text(
      yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
      encoding="utf-8",
    )
    logger.info("config_saved", path=str(target))
    return target

  @classmethod
  def save_mcp_servers(cls, mcp_servers: dict[str, Any]) -> Path:
    """Save the MCP servers dictionary to the side-car file.

    Args:
      mcp_servers: Mapping from server name to server configuration dict.

    Returns:
      Path to the written file.
    """
    import yaml

    cls.MCP_SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcp_servers": mcp_servers}
    cls.MCP_SERVERS_PATH.write_text(
      yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
      encoding="utf-8",
    )
    logger.info("saved_mcp_servers", path=str(cls.MCP_SERVERS_PATH))
    return cls.MCP_SERVERS_PATH

  @classmethod
  def load_mcp_servers(cls) -> dict[str, Any]:
    """Load only the MCP servers side-car configuration.

    Returns:
      The ``mcp_servers`` dict, or an empty dict if the file does not exist.
    """
    data = cls._load_yaml(cls.MCP_SERVERS_PATH)
    return data.get("mcp_servers", {})


def load_config(path: Path | str | None = None) -> CLIConfig:
  """Convenience function: load CLI configuration."""
  return ConfigLoader.load(path)
