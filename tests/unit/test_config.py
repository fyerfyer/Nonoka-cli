"""Tests for config loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nonoka_cli.config.loader import ConfigLoader, _substitute_env_vars, load_config
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import ConfigError, ConfigNotFoundError


class TestSubstituteEnvVars:
  """Tests for environment variable substitution logic."""

  def test_no_substitution_for_plain_string(self):
    result = _substitute_env_vars("hello world")
    assert result == "hello world"

  def test_substitutes_existing_env_var(self, monkeypatch):
    monkeypatch.setenv("NONOKA_TEST_VAR", "test_value")
    result = _substitute_env_vars("${NONOKA_TEST_VAR}")
    assert result == "test_value"

  def test_substitutes_env_var_with_default_when_set(self, monkeypatch):
    monkeypatch.setenv("NONOKA_TEST_VAR", "real_value")
    result = _substitute_env_vars("${NONOKA_TEST_VAR:-default}")
    assert result == "real_value"

  def test_uses_default_when_env_var_missing(self):
    os.environ.pop("NONOKA_TEST_MISSING", None)
    result = _substitute_env_vars("${NONOKA_TEST_MISSING:-fallback}")
    assert result == "fallback"

  def test_raises_when_env_var_missing_and_no_default(self):
    os.environ.pop("NONOKA_TEST_MISSING", None)
    with pytest.raises(ConfigError, match="not set and no default"):
      _substitute_env_vars("${NONOKA_TEST_MISSING}")

  def test_substitutes_nested_in_dict(self, monkeypatch):
    monkeypatch.setenv("MODEL", "gpt-4o")
    data = {"model": "${MODEL}", "other": "static"}
    result = _substitute_env_vars(data)
    assert result == {"model": "gpt-4o", "other": "static"}

  def test_substitutes_in_list(self, monkeypatch):
    monkeypatch.setenv("TOOL", "write_file")
    data = ["${TOOL}", "static"]
    result = _substitute_env_vars(data)
    assert result == ["write_file", "static"]

  def test_recursive_substitution(self, monkeypatch):
    monkeypatch.setenv("A", "alpha")
    monkeypatch.setenv("B", "beta")
    data = {"nested": {"list": ["${A}", "${B}"]}}
    result = _substitute_env_vars(data)
    assert result == {"nested": {"list": ["alpha", "beta"]}}


class TestConfigLoader:
  """Tests for ConfigLoader class."""

  def test_find_config_file_with_explicit_path(self, temp_config_file):
    temp_config_file.write_text("model: gpt-4o\n")
    found = ConfigLoader.find_config_file(temp_config_file)
    assert found == temp_config_file

  def test_find_config_file_raises_when_explicit_missing(self):
    with pytest.raises(ConfigNotFoundError, match="Explicit config file not found"):
      ConfigLoader.find_config_file("/nonexistent/path/config.yaml")

  def test_load_valid_config(self, temp_config_file, valid_config_yaml):
    temp_config_file.write_text(valid_config_yaml)
    config = ConfigLoader.load(temp_config_file)
    assert config.model == "gpt-4o"
    assert "helpful assistant" in config.system_prompt
    assert config.cli.theme == "dark"
    assert config.cli.max_history == 500
    assert config.hitl.policy == "interactive"
    assert config.hitl.dangerous_tools == ["write_file"]

  def test_load_empty_config_uses_defaults(self, temp_config_file):
    temp_config_file.write_text("")
    config = ConfigLoader.load(temp_config_file)
    assert config.model == "gpt-4o"  # default
    assert config.system_prompt == ""  # default; AgentFactory supplies coding prompt

  def test_load_with_env_substitution(self, temp_config_file, config_with_env_vars, monkeypatch):
    temp_config_file.write_text(config_with_env_vars)
    monkeypatch.setenv("NONOKA_TEST_MODEL", "deepseek-chat")
    monkeypatch.setenv("NONOKA_TEST_USER", "tester")
    config = ConfigLoader.load(temp_config_file)
    assert config.model == "deepseek-chat"
    assert "Hello tester" in config.system_prompt

  def test_load_with_env_default_fallback(self, temp_config_file, config_with_env_vars):
    os.environ.pop("NONOKA_TEST_MODEL", None)
    os.environ.pop("NONOKA_TEST_USER", None)
    temp_config_file.write_text(config_with_env_vars)
    config = ConfigLoader.load(temp_config_file)
    assert config.model == "gpt-4o"
    assert "Hello world" in config.system_prompt

  def test_load_raises_on_invalid_yaml(self, temp_config_file):
    temp_config_file.write_text("{invalid yaml: [")
    with pytest.raises(ConfigError, match="Failed to parse YAML"):
      ConfigLoader.load(temp_config_file)

  def test_load_raises_on_non_dict_yaml(self, temp_config_file):
    temp_config_file.write_text("- just\n- a\n- list")
    with pytest.raises(ConfigError, match="top-level object"):
      ConfigLoader.load(temp_config_file)

  def test_validation_error_includes_location_and_suggestion(self, temp_config_file):
    temp_config_file.write_text("model: gpt-4o\ncli:\n  max_history: not_a_number\n")
    with pytest.raises(ConfigError) as exc_info:
      ConfigLoader.load(temp_config_file)

    message = str(exc_info.value)
    assert "Config validation failed" in message
    assert "cli.max_history" in message
    assert "integer" in message.lower() or "int_parsing" in message

  def test_validation_error_suggests_fix_for_missing_field(self, temp_config_file):
    # Trigger a missing-field error on a required nested field
    temp_config_file.write_text("mcp_servers:\n  fs:\n    command: npx\n")
    with pytest.raises(ConfigError) as exc_info:
      ConfigLoader.load(temp_config_file)

    message = str(exc_info.value)
    assert "mcp_servers.fs.transport" in message or "transport" in message


class TestCLIConfigModel:
  """Tests for CLIConfig pydantic model."""

  def test_default_values(self):
    config = CLIConfig()
    assert config.model == "gpt-4o"
    assert config.system_prompt == ""
    assert config.mcp_servers == {}
    assert config.tool_paths == []
    assert config.skills == []
    assert config.cli.theme == "dark"
    assert config.hitl.policy == "interactive"

  def test_custom_values(self):
    config = CLIConfig(
      model="deepseek-chat",
      system_prompt="Custom prompt.",
    )
    assert config.model == "deepseek-chat"
    assert config.system_prompt == "Custom prompt."

  def test_tool_paths_expanded(self):
    config = CLIConfig(tool_paths=["~/tools"])
    assert len(config.tool_paths) == 1
    assert config.tool_paths[0] == Path.home() / "tools"


class TestLoadConfigConvenience:
  """Tests for the load_config convenience function."""

  def test_load_config_returns_cli_config(self, temp_config_file):
    temp_config_file.write_text("model: custom-model\n")
    config = load_config(temp_config_file)
    assert isinstance(config, CLIConfig)
    assert config.model == "custom-model"


class TestMCPServersSidecar:
  """Tests for the optional mcp_servers.yaml side-car file."""

  @pytest.fixture
  def temp_mcp_file(self, tmp_path, monkeypatch):
    """Patch the MCP side-car path to a temp file."""
    path = tmp_path / "mcp_servers.yaml"
    monkeypatch.setattr(ConfigLoader, "MCP_SERVERS_PATH", path)
    return path

  def test_sidecar_merges_into_main_config(self, temp_config_file, temp_mcp_file):
    temp_config_file.write_text(
      "model: gpt-4o\nmcp_servers:\n  main:\n    transport: stdio\n    command: echo\n"
    )
    temp_mcp_file.write_text(
      "mcp_servers:\n  sidecar:\n    transport: stdio\n    command: cat\n"
    )
    config = ConfigLoader.load(temp_config_file)
    assert "main" in config.mcp_servers
    assert "sidecar" in config.mcp_servers
    assert config.mcp_servers["sidecar"].command == "cat"

  def test_sidecar_overrides_main_config(self, temp_config_file, temp_mcp_file):
    temp_config_file.write_text(
      "model: gpt-4o\nmcp_servers:\n  shared:\n    transport: stdio\n    command: echo\n"
    )
    temp_mcp_file.write_text(
      "mcp_servers:\n  shared:\n    transport: stdio\n    command: cat\n"
    )
    config = ConfigLoader.load(temp_config_file)
    assert config.mcp_servers["shared"].command == "cat"

  def test_save_and_load_mcp_servers(self, temp_mcp_file):
    ConfigLoader.save_mcp_servers({
      "fetch": {"transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]},
    })
    loaded = ConfigLoader.load_mcp_servers()
    assert "fetch" in loaded
    assert loaded["fetch"]["command"] == "uvx"
