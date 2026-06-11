"""Configuration loading with env-var substitution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError, ConfigNotFoundError

logger = structlog.get_logger("nonoka_cli.config")

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-?(?P<default>[^}]*))?\}")


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
        raise ConfigError(
          f"Environment variable '{var_name}' is not set and no default provided"
        )
      return result
    return _ENV_PATTERN.sub(replacer, value)
  if isinstance(value, dict):
    return {k: _substitute_env_vars(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_substitute_env_vars(item) for item in value]
  return value


class ConfigLoader:
  """Loads and validates CLI configuration from YAML files."""

  DEFAULT_PATH = Path.home() / ".config" / "nonoka" / "config.yaml"
  FALLBACK_PATH = Path.cwd() / "nonoka.yaml"

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

    if cls.FALLBACK_PATH.exists():
      return cls.FALLBACK_PATH

    raise ConfigNotFoundError(
      f"No config file found. Searched: {cls.DEFAULT_PATH}, {cls.FALLBACK_PATH}"
    )

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
      import yaml
    except ImportError as exc:
      raise ConfigError("PyYAML is required. Install: pip install pyyaml") from exc

    try:
      raw = config_path.read_text(encoding="utf-8")
      data = yaml.safe_load(raw)
    except Exception as exc:
      raise ConfigError(f"Failed to parse YAML: {exc}") from exc

    if data is None:
      data = {}
    if not isinstance(data, dict):
      raise ConfigError(
        f"Config file must contain a top-level object, got {type(data).__name__}"
      )

    # Substitute env vars before validation
    try:
      data = _substitute_env_vars(data)
    except ConfigError:
      raise
    except Exception as exc:
      raise ConfigError(f"Environment variable substitution failed: {exc}") from exc

    try:
      config = CLIConfig.model_validate(data)
    except Exception as exc:
      raise ConfigError(f"Config validation failed: {exc}") from exc

    logger.info("config_loaded", model=config.model)
    return config


def load_config(path: Path | str | None = None) -> CLIConfig:
  """Convenience function: load CLI configuration."""
  return ConfigLoader.load(path)
