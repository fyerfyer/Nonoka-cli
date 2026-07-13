"""Tests for Orchestrator two-stage planning integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator


@pytest.fixture
def base_orchestrator(tmp_path: Path) -> Orchestrator:
  """Return a minimally initialized orchestrator with mocked services."""
  config = CLIConfig(
    model="gpt-4o",
    agents={
      "planner": {"model": "gpt-4o-mini", "max_turns": 3, "system_prompt": ""},
      "executor": {"model": "gpt-4o", "max_turns": 10, "system_prompt": ""},
    },
  )
  orch = Orchestrator(config=config)
  orch._initialized = True
  orch._session_service = MagicMock()
  orch._session_service.current_id = "test-session"
  orch._session_service.manager = SimpleNamespace(db_path=tmp_path / "nonoka.db")
  orch._runner_service = MagicMock()
  orch._agent_factory = MagicMock()
  orch._agent_factory.get_agent.return_value = MagicMock()
  orch._config = config
  return orch


@pytest.mark.asyncio
async def test_execute_with_external_tools_injects_execution_plan(
  base_orchestrator: Orchestrator,
) -> None:
  """When planning is enabled, the generated plan is passed to the agent factory."""
  orch = base_orchestrator

  planning_service = MagicMock()
  planning_service.enabled = True
  planning_service.planner_model = "gpt-4o-mini"
  planning_service.plan = AsyncMock(return_value="1. Do X\n2. Do Y")
  orch._planning_service = planning_service

  async def _fake_stream(*args, **kwargs):
    yield MagicMock(type="final", data={"success": True})

  orch._runner_service.run = MagicMock(side_effect=_fake_stream)

  events = []
  async for event in orch.execute_with_external_tools(
    prompt="Create a test file",
    tools=[],
    working_dir=Path.cwd(),
    host_system_prompt="Host prompt",
  ):
    events.append(event)

  planning_service.plan.assert_awaited_once_with("Create a test file")
  call_kwargs = orch._agent_factory.build_with_external_tools.call_args.kwargs
  assert call_kwargs.get("execution_plan") == "1. Do X\n2. Do Y"


@pytest.mark.asyncio
async def test_execute_with_external_tools_skips_planning_when_disabled(
  base_orchestrator: Orchestrator,
) -> None:
  """When no planner model is configured, no plan is generated."""
  orch = base_orchestrator

  planning_service = MagicMock()
  planning_service.enabled = False
  planning_service.planner_model = ""
  planning_service.plan = AsyncMock(return_value="should not be used")
  orch._planning_service = planning_service

  async def _fake_stream(*args, **kwargs):
    yield MagicMock(type="final", data={"success": True})

  orch._runner_service.run = MagicMock(side_effect=_fake_stream)

  events = []
  async for event in orch.execute_with_external_tools(
    prompt="Create a test file",
    tools=[],
    working_dir=Path.cwd(),
    host_system_prompt="Host prompt",
  ):
    events.append(event)

  planning_service.plan.assert_not_awaited()
  call_kwargs = orch._agent_factory.build_with_external_tools.call_args.kwargs
  assert call_kwargs.get("execution_plan") is None
