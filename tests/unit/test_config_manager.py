"""Tests for configuration manager with hot-reload support."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError


class TestConfigManager:
  """Tests for ConfigManager."""

  @pytest.fixture
  def sample_config(self):
    return CLIConfig(
      model="gpt-4o",
      system_prompt="You are a test assistant.",
    )

  @pytest.fixture
  def manager(self, sample_config):
    return ConfigManager(sample_config)

  def test_get_returns_config(self, manager, sample_config):
    assert manager.get() is sample_config
    assert manager.config is sample_config

  def test_config_path_defaults_to_none(self, manager):
    assert manager.config_path is None

  def test_load_from_explicit_path(self, temp_config_file):
    temp_config_file.write_text("model: deepseek-chat\nsystem_prompt: Hello\n")
    manager = ConfigManager.load(temp_config_file)
    assert manager.config.model == "deepseek-chat"
    assert manager.config.system_prompt == "Hello"
    assert manager.config_path == temp_config_file

  def test_load_from_default_location(self, temp_config_file, monkeypatch):
    temp_config_file.write_text("model: deepseek-chat\n")
    monkeypatch.setattr(
      ConfigLoader, "DEFAULT_PATH", temp_config_file
    )
    monkeypatch.setattr(
      ConfigLoader, "FALLBACK_PATH", Path("/nonexistent/nonoka.yaml")
    )
    manager = ConfigManager.load()
    assert manager.config.model == "deepseek-chat"
    assert manager.config_path == temp_config_file

  def test_reload_updates_config(self, temp_config_file):
    temp_config_file.write_text("model: gpt-4o\nsystem_prompt: Old\n")
    manager = ConfigManager.load(temp_config_file)

    # Modify file
    temp_config_file.write_text("model: gpt-4o-mini\nsystem_prompt: New\n")

    new_config = manager.reload()
    assert new_config.model == "gpt-4o-mini"
    assert new_config.system_prompt == "New"
    assert manager.get().model == "gpt-4o-mini"

  def test_reload_notifies_listeners(self, temp_config_file):
    temp_config_file.write_text("model: gpt-4o\n")
    manager = ConfigManager.load(temp_config_file)

    called_with = []
    manager.on_change(lambda cfg: called_with.append(cfg.model))

    temp_config_file.write_text("model: gpt-4o-mini\n")
    manager.reload()

    assert called_with == ["gpt-4o-mini"]

  def test_reload_without_path_raises(self, manager):
    with pytest.raises(ConfigError, match="no config file path"):
      manager.reload()

  def test_reload_validation_error_propagates(self, temp_config_file):
    temp_config_file.write_text("model: gpt-4o\n")
    manager = ConfigManager.load(temp_config_file)

    # Now write invalid config
    temp_config_file.write_text("model: gpt-4o\ncli:\n  max_history: not_a_number\n")
    with pytest.raises(ConfigError, match="Config validation failed"):
      manager.reload()

  def test_remove_listener(self, manager):
    called = []
    callback = lambda cfg: called.append(True)
    manager.on_change(callback)
    assert manager.remove_listener(callback) is True
    assert manager.remove_listener(callback) is False
