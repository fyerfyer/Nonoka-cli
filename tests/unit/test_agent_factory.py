"""Tests for the Agent factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.tools.loader import ToolLoader
from nonoka_cli.utils.errors import AgentBuildError


class TestAgentFactoryBuild:
  """Tests for AgentFactory.build()."""

  def test_build_injects_model_into_system_prompt(self):
    config = CLIConfig(model="deepseek-chat", system_prompt="You are helpful.")
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      agent = factory.build()

    assert agent is mock_agent
    _, kwargs = builder.system_prompt.call_args
    effective_prompt = kwargs[0] if kwargs else builder.system_prompt.call_args[0][0]
    assert "deepseek-chat" in effective_prompt
    assert "Your current model is: deepseek-chat" in effective_prompt

  def test_build_does_not_duplicate_identity_line(self):
    config = CLIConfig(
      model="gpt-4o",
      system_prompt="You are helpful.\n\nYour current model is: gpt-4o.",
    )
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      factory.build()

    _, kwargs = builder.system_prompt.call_args
    effective_prompt = kwargs[0] if kwargs else builder.system_prompt.call_args[0][0]
    assert effective_prompt.count("Your current model is: gpt-4o") == 1

  def test_build_raises_when_model_missing(self):
    config = CLIConfig(model="", system_prompt="You are helpful.")
    factory = AgentFactory(config)

    with pytest.raises(AgentBuildError, match="No model configured"):
      factory.build()

  def test_build_uses_coding_aware_default_prompt(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      factory.build()

    _, kwargs = builder.system_prompt.call_args
    effective_prompt = kwargs[0] if kwargs else builder.system_prompt.call_args[0][0]
    assert "autonomous ai assistant" in effective_prompt
    assert "view" in effective_prompt
    assert "execute_command" in effective_prompt


class TestAgentFactoryRebuild:
  """Tests for AgentFactory.rebuild()."""

  def test_rebuild_applies_config_patch(self):
    config = CLIConfig(model="gpt-4o", system_prompt="You are helpful.")
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      factory.rebuild({"model": "gpt-4o-mini"})

    builder.model.assert_called_once_with("gpt-4o-mini")
    assert factory.config.model == "gpt-4o-mini"

  def test_rebuild_without_patch_uses_current_config(self):
    config = CLIConfig(model="deepseek-chat", system_prompt="You are helpful.")
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      factory.rebuild()

    builder.model.assert_called_once_with("deepseek-chat")


class TestAgentFactoryGetAgent:
  """Tests for AgentFactory.get_agent()."""

  def test_get_agent_returns_none_before_build(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config)
    assert factory.get_agent() is None

  def test_get_agent_returns_built_agent(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config)

    mock_agent = MagicMock()
    with patch("nonoka_cli.core.agent_factory.AgentBuilder") as mock_builder_cls:
      builder = mock_builder_cls.return_value
      builder.model.return_value = builder
      builder.system_prompt.return_value = builder
      builder.max_turns.return_value = builder
      builder.build.return_value = mock_agent

      factory.build()

    assert factory.get_agent() is mock_agent


class TestAgentFactoryTools:
  """Tests for AgentFactory tool integration."""

  def test_build_with_tool_loader_includes_local_tools(self, tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "local.py").write_text(
      "from nonoka import tool\n\n@tool\ndef local_tool() -> str:\n  return 'ok'\n"
    )

    config = CLIConfig(model="gpt-4o")
    loader = ToolLoader([tools_dir], include_builtins=False)
    factory = AgentFactory(config, tool_loader=loader)
    agent = factory.build()

    assert any(t.name == "local_tool" for t in agent.tools)

  def test_list_all_tools_without_build(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config, tool_loader=ToolLoader([], include_builtins=True))
    tools = factory.list_all_tools()

    assert any(t.name == "read_file" for t in tools)

  def test_get_tool_returns_named_tool(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config, tool_loader=ToolLoader([], include_builtins=True))

    tool = factory.get_tool("read_file")
    assert tool is not None
    assert tool.name == "read_file"

  def test_get_tool_returns_none_for_unknown(self):
    config = CLIConfig(model="gpt-4o")
    factory = AgentFactory(config, tool_loader=ToolLoader([], include_builtins=False))

    assert factory.get_tool("missing") is None
