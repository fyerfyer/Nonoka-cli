"""Configuration manager with hot-reload support.

Provides change notifications and safe re-loading of configuration
while keeping the CLI running.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Callable

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.config")

ChangeCallback = Callable[[CLIConfig], None]


class ConfigManager:
  """Manages configuration with hot-reload and change notification.

  Usage::

    manager = ConfigManager.load()
    config = manager.get()

    # Register a listener
    manager.on_change(lambda cfg: print(f"Config reloaded: {cfg.model}"))

    # Trigger reload
    new_config = manager.reload()
  """

  @classmethod
  def load(cls, path: Path | str | None = None) -> ConfigManager:
    """Load configuration and create a manager.

    Args:
      path: Explicit config file path. If None, searches default locations.

    Returns:
      ConfigManager with loaded configuration.
    """
    config = ConfigLoader.load(path)
    config_path = None
    if path is not None:
      config_path = Path(path)
    else:
      try:
        config_path = ConfigLoader.find_config_file()
      except ConfigError:
        config_path = None

    return cls(config, config_path)

  def __init__(self, config: CLIConfig, config_path: Path | None = None):
    """Initialize the manager.

    Args:
      config: Initial configuration.
      config_path: Path to the config file (for reloading).
    """
    self._config = config
    self._config_path = config_path
    self._listeners: list[ChangeCallback] = []

  @property
  def config(self) -> CLIConfig:
    """Current configuration."""
    return self._config

  @property
  def config_path(self) -> Path | None:
    """Path to the loaded config file."""
    return self._config_path

  def get(self) -> CLIConfig:
    """Get current configuration (alias for ``config`` property)."""
    return self._config

  def reload(self) -> CLIConfig:
    """Reload configuration from disk and notify listeners.

    Returns:
      The newly loaded configuration.

    Raises:
      ConfigError: If config file is missing or validation fails.
    """
    if self._config_path is None:
      raise ConfigError(
        "Cannot reload: no config file path is known. "
        "Use ConfigManager.load() with an explicit path."
      )

    logger.info("reloading_config", path=str(self._config_path))

    try:
      new_config = ConfigLoader.load(self._config_path)
    except ConfigError:
      raise
    except Exception as exc:
      raise ConfigError(f"Failed to reload configuration: {exc}") from exc

    old_model = self._config.model
    old_system_prompt = self._config.system_prompt

    self._config = new_config

    logger.info(
      "config_reloaded",
      path=str(self._config_path),
      model_changed=new_config.model != old_model,
      prompt_changed=new_config.system_prompt != old_system_prompt,
    )

    # Notify listeners
    for callback in self._listeners:
      try:
        callback(new_config)
      except Exception as exc:
        logger.warning("config_listener_failed", error=str(exc))

    return new_config

  def on_change(self, callback: ChangeCallback) -> None:
    """Register a callback to be invoked when config is reloaded.

    Args:
      callback: Function receiving the new CLIConfig.
    """
    self._listeners.append(callback)

  def remove_listener(self, callback: ChangeCallback) -> bool:
    """Remove a previously registered change listener.

    Args:
      callback: The callback to remove.

    Returns:
      True if the callback was found and removed.
    """
    try:
      self._listeners.remove(callback)
      return True
    except ValueError:
      return False
