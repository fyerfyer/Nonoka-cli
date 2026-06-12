"""Tests for core orchestration layer."""

from __future__ import annotations

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka import Agent
from nonoka.core.runner import StreamEvent

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.utils.errors import AgentBuildError, ConfigError, OrchestratorError


class TestAgentFactory:
  """Tests for AgentFactory."""

  def test_build_creates_agent(self, sample_config):
    factory = AgentFactory(sample_config)
    agent = factory.build()
    assert isinstance(agent, Agent)
    assert agent.model == "gpt-4o"
    assert "You are a test assistant." in agent.system_prompt
    assert "Your current model is: gpt-4o" in agent.system_prompt

  def test_build_raises_when_model_missing(self):
    config = CLIConfig(model="")
    factory = AgentFactory(config)
    with pytest.raises(AgentBuildError, match="No model configured"):
      factory.build()

  def test_get_agent_returns_none_before_build(self, sample_config):
    factory = AgentFactory(sample_config)
    assert factory.get_agent() is None

  def test_get_agent_returns_built_agent(self, sample_config):
    factory = AgentFactory(sample_config)
    built = factory.build()
    assert factory.get_agent() is built

  def test_rebuild_without_patch(self, sample_config):
    factory = AgentFactory(sample_config)
    agent1 = factory.build()
    agent2 = factory.rebuild()
    assert isinstance(agent2, Agent)
    assert agent2.model == "gpt-4o"

  def test_rebuild_with_model_patch(self, sample_config):
    factory = AgentFactory(sample_config)
    factory.build()
    agent2 = factory.rebuild(config_patch={"model": "deepseek-chat"})
    assert agent2.model == "deepseek-chat"
    assert factory.config.model == "deepseek-chat"

  def test_config_property(self, sample_config):
    factory = AgentFactory(sample_config)
    assert factory.config is sample_config


class TestCLIContext:
  """Tests for CLIContext dataclass."""

  def test_creation(self, sample_config):
    from pathlib import Path
    ctx = CLIContext(
      user="local",
      session_id="test-session",
      config=sample_config,
      working_dir=Path("/tmp"),
    )
    assert ctx.user == "local"
    assert ctx.session_id == "test-session"
    assert ctx.config.model == "gpt-4o"
    assert ctx.working_dir == Path("/tmp")


class TestOrchestrator:
  """Tests for Orchestrator."""

  @pytest.fixture
  def mock_config(self):
    return CLIConfig(model="gpt-4o", system_prompt="Test.")

  @pytest.fixture
  def orchestrator(self, mock_config):
    return Orchestrator(config=mock_config)

  def test_init_with_config(self, mock_config):
    orch = Orchestrator(config=mock_config)
    assert orch.config is mock_config
    assert orch.session_id

  def test_init_without_config(self):
    orch = Orchestrator()
    with pytest.raises(OrchestratorError, match="not initialized"):
      _ = orch.config

  def test_session_id_is_uuid(self, orchestrator):
    import uuid
    uuid.UUID(orchestrator.session_id)  # raises ValueError if invalid

  @pytest.mark.asyncio
  async def test_new_session_changes_session_id(self, orchestrator):
    old_id = orchestrator.session_id
    new_id = await orchestrator.new_session()
    assert new_id != old_id
    assert orchestrator.session_id == new_id

  @pytest.mark.asyncio
  async def test_initialize_builds_agent_and_runner(self, orchestrator):
    await orchestrator.initialize()
    assert orchestrator._agent_factory is not None
    assert orchestrator._runner is not None
    assert orchestrator._initialized is True
    assert orchestrator._agent_factory.get_agent() is not None

  @pytest.mark.asyncio
  async def test_initialize_with_config_path(self, mock_config, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: deepseek-chat\nsystem_prompt: Hello\n")
    orch = Orchestrator()
    await orch.initialize(config_path=config_file)
    assert orch.config.model == "deepseek-chat"

  @pytest.mark.asyncio
  async def test_initialize_raises_on_bad_config(self, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("{invalid")
    orch = Orchestrator()
    with pytest.raises(ConfigError):
      await orch.initialize(config_path=config_file)

  @pytest.mark.asyncio
  async def test_execute_raises_when_not_initialized(self, orchestrator):
    with pytest.raises(OrchestratorError, match="not initialized"):
      async for _ in orchestrator.execute("hello"):
        pass

  @pytest.mark.asyncio
  async def test_execute_yields_events(self, orchestrator):
    await orchestrator.initialize()

    mock_event = StreamEvent(type="content_delta", data={"content": "hi"})
    final_event = StreamEvent(type="final", data={"success": True})

    with patch.object(
      orchestrator._runner,
      "run_react_stream",
      return_value=async_event_iter([mock_event, final_event]),
    ):
      events = []
      async for event in orchestrator.execute("hello"):
        events.append(event)

    assert len(events) == 2
    assert events[0].type == "content_delta"
    assert events[1].type == "final"

  @pytest.mark.asyncio
  async def test_execute_injects_cli_context(self, orchestrator):
    await orchestrator.initialize()

    captured = []

    async def mock_run_stream(agent, prompt, deps, session_id):
      captured.append(deps)
      yield StreamEvent(type="final", data={"success": True})

    with patch.object(orchestrator._runner, "run_react_stream", side_effect=mock_run_stream):
      async for _ in orchestrator.execute("test"):
        pass

    assert len(captured) == 1
    assert isinstance(captured[0], CLIContext)
    assert captured[0].user == "local"
    assert captured[0].session_id == orchestrator.session_id

  @pytest.mark.asyncio
  async def test_shutdown_sets_initialized_false(self, orchestrator):
    await orchestrator.initialize()
    assert orchestrator._initialized is True
    await orchestrator.shutdown()
    assert orchestrator._initialized is False

  @pytest.mark.asyncio
  async def test_switch_model_rebuilds_agent_and_keeps_session(self, orchestrator):
    await orchestrator.initialize()
    old_session_id = orchestrator.session_id
    old_agent = orchestrator._agent_factory.get_agent()

    await orchestrator.switch_model("deepseek-chat")

    new_agent = orchestrator._agent_factory.get_agent()
    assert new_agent is not old_agent
    assert new_agent.model == "deepseek-chat"
    assert orchestrator.session_id == old_session_id
    assert orchestrator.config.model == "deepseek-chat"

  @pytest.mark.asyncio
  async def test_switch_model_empty_raises(self, orchestrator):
    await orchestrator.initialize()
    with pytest.raises(ConfigError, match="empty"):
      await orchestrator.switch_model("")

  @pytest.mark.asyncio
  async def test_switch_model_rollback_on_build_failure(self, orchestrator):
    await orchestrator.initialize()
    original_model = orchestrator.config.model

    # Patch AgentFactory.rebuild to fail
    with patch.object(
      orchestrator._agent_factory,
      "rebuild",
      side_effect=RuntimeError("build failed"),
    ):
      with pytest.raises(OrchestratorError, match="Failed to switch model"):
        await orchestrator.switch_model("some-model")

    assert orchestrator.config.model == original_model

  @pytest.mark.asyncio
  async def test_reload_config_updates_agent(self, orchestrator, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4o\nsystem_prompt: Old\n")

    orch = Orchestrator()
    await orch.initialize(config_path=config_file)
    old_session_id = orch.session_id

    config_file.write_text("model: gpt-4o-mini\nsystem_prompt: New\n")
    new_config = await orch.reload_config()

    assert new_config.model == "gpt-4o-mini"
    assert orch.config.model == "gpt-4o-mini"
    assert orch.session_id == old_session_id
    assert orch._agent_factory.get_agent().model == "gpt-4o-mini"

  @pytest.mark.asyncio
  async def test_reload_config_failure_keeps_current_config(self, orchestrator, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4o\nsystem_prompt: Old\n")

    orch = Orchestrator()
    await orch.initialize(config_path=config_file)

    config_file.write_text("model: gpt-4o\ncli:\n  max_history: not_a_number\n")
    with pytest.raises(ConfigError, match="Config validation failed"):
      await orch.reload_config()

    assert orch.config.model == "gpt-4o"
    assert orch._agent_factory.get_agent().model == "gpt-4o"

  @pytest.mark.asyncio
  async def test_config_manager_available_after_initialize(self, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4o\n")
    orch = Orchestrator()
    await orch.initialize(config_path=config_file)
    assert orch.config_manager is not None
    assert orch.config_manager.config_path == config_file


async def async_event_iter(events):
  """Helper to create an async iterator from a list of events."""
  for e in events:
    yield e
