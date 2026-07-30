from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nonoka import Agent, Runner
from nonoka.core.context import RunContext

from nonoka_cli.core.plugin_manifest import AgentEntry, DynamicAgentEntry
from nonoka_cli.core.project_agents import (
  DynamicAgentDefinition,
  ProjectAgentDefinition,
  compile_project_agents,
)
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy


def _definition(**overrides) -> ProjectAgentDefinition:
  values = {
    "name": "planner",
    "description": "Plan a bounded change.",
    "model": "child-model",
    "system_prompt": "Return a concise plan.",
    "max_turns": 2,
    "max_invocations": 2,
    "allowed_tools": [],
    "output_contract": "text",
  }
  values.update(overrides)
  return ProjectAgentDefinition(
    entry=AgentEntry(**values),
    source=Path("/workspace/.nonoka/plugin.json"),
  )


def test_compile_project_agent_is_bounded_and_tool_free() -> None:
  compiled = compile_project_agents([_definition()], ToolOutputPolicy())

  assert not compiled.errors
  assert len(compiled.tools) == 1
  tool = compiled.tools[0]
  assert tool.name == "agent__planner"
  assert tool.agent.model == "child-model"
  assert tool.agent.system_prompt == "Return a concise plan."
  assert tool.agent.max_turns == 2
  assert tool.agent.max_steps == 0
  assert list(tool.agent.tools) == []
  assert tool.max_depth == 1
  assert tool.execution.parallel_safe is False
  assert tool.metadata["source"].endswith(".nonoka/plugin.json")


def test_compile_errors_disable_all_project_agents() -> None:
  compiled = compile_project_agents(
    [_definition(), _definition(name="bad role", model="")],
    ToolOutputPolicy(),
  )

  assert compiled.errors
  assert compiled.tools == []


def test_allowed_tools_warns_but_never_grants_child_tools() -> None:
  compiled = compile_project_agents(
    [_definition(allowed_tools=["read", "bash"])],
    ToolOutputPolicy(),
  )

  assert not compiled.errors
  assert any(item.level == "warning" for item in compiled.diagnostics)
  assert list(compiled.tools[0].agent.tools) == []


@pytest.mark.asyncio
async def test_review_contract_separates_blocking_issues_from_suggestions() -> None:
  tool = compile_project_agents([_definition(output_contract="review")], ToolOutputPolicy()).tools[
    0
  ]
  provider = MagicMock()
  provider.chat = AsyncMock(
    return_value=MagicMock(
      content=(
        '{"verdict":"CHANGES_REQUIRED","blocking_issues":[],'
        '"non_blocking_suggestions":["Add cross-process locking"]}'
      ),
      tool_calls=None,
      usage={},
    )
  )
  runner = Runner(checkpoint="memory", memory="in_memory")
  runner._create_llm = lambda _agent: provider  # type: ignore[method-assign]
  parent = await runner._create_session(Agent(model="parent", tools=[]), deps=None)

  result = await tool.invoke(RunContext(parent), {"task": "review"})

  assert result["result"]["verdict"] == "APPROVED"
  assert result["result"]["blocking_issues"] == []
  assert "explicit acceptance criterion" in tool.agent.system_prompt


@pytest.mark.asyncio
async def test_project_agent_invocation_limit_is_parent_session_scoped() -> None:
  tool = compile_project_agents([_definition(max_invocations=1)], ToolOutputPolicy()).tools[0]
  runner = Runner(checkpoint="memory")
  parent = await runner._create_session(Agent(model="parent", tools=[]), deps=None)
  parent.extension_state["project_agents"] = {tool.name: 1}

  result = await tool.invoke(RunContext(parent), {"task": "plan"})

  assert result["success"] is False
  assert result["error_type"] == "invocation_limit"


@pytest.mark.asyncio
async def test_project_agent_returns_structured_success() -> None:
  tool = compile_project_agents([_definition()], ToolOutputPolicy()).tools[0]
  provider = MagicMock()
  provider.chat = AsyncMock(
    return_value=MagicMock(content="bounded plan", tool_calls=None, usage={})
  )
  runner = Runner(checkpoint="memory", memory="in_memory")
  runner._create_llm = lambda agent: provider  # type: ignore[method-assign]
  runner.llm = provider
  parent = await runner._create_session(Agent(model="parent", tools=[]), deps=None)

  result = await tool.invoke(RunContext(parent), {"task": "plan the change"})

  assert result["role"] == "planner"
  assert result["success"] is True
  assert result["result"] == "bounded plan"
  assert result["session_id"] != parent.session_id


def _dynamic_definition(**overrides) -> DynamicAgentDefinition:
  values = {
    "enabled": True,
    "model": "approved-child-model",
    "base_system_prompt": "You are a bounded advisor.",
    "max_turns": 2,
    "max_invocations": 1,
  }
  values.update(overrides)
  return DynamicAgentDefinition(
    entry=DynamicAgentEntry(**values),
    source=Path("/workspace/.nonoka/plugin.json"),
  )


def test_dynamic_agent_schema_exposes_no_authority_bearing_arguments() -> None:
  compiled = compile_project_agents([], ToolOutputPolicy(), _dynamic_definition())

  assert not compiled.errors
  tool = compiled.tools[0]
  assert tool.name == "agent__spawn"
  assert set(tool.parameters["properties"]) == {"role", "instructions", "task", "context"}
  assert "model" not in tool.parameters["properties"]
  assert "tools" not in tool.parameters["properties"]
  assert tool.execution.parallel_safe is False


@pytest.mark.asyncio
async def test_dynamic_agent_is_fixed_model_tool_free_and_limited() -> None:
  tool = compile_project_agents([], ToolOutputPolicy(), _dynamic_definition()).tools[0]
  provider = MagicMock()
  captured_agents = []
  provider.chat = AsyncMock(
    return_value=MagicMock(content="bounded advice", tool_calls=None, usage={})
  )
  runner = Runner(checkpoint="memory", memory="in_memory")

  def create_provider(agent):
    captured_agents.append(agent)
    return provider

  runner._create_llm = create_provider  # type: ignore[method-assign]
  parent = await runner._create_session(Agent(model="parent", tools=[]), deps=None)
  arguments = {
    "role": "reviewer",
    "instructions": "Check the explicit requirement.",
    "task": "Review this result.",
    "context": "Requirement: complete is accepted once.",
  }

  result = await tool.invoke(RunContext(parent), arguments)
  limited = await tool.invoke(RunContext(parent), arguments)

  child = captured_agents[-1]
  assert child.model == "approved-child-model"
  assert child.max_turns == 2
  assert child.max_steps == 0
  assert list(child.tools) == []
  assert result["success"] is True
  assert result["result"] == "bounded advice"
  assert limited["error_type"] == "invocation_limit"


@pytest.mark.asyncio
async def test_dynamic_agent_rejects_oversized_input_without_spending_invocation() -> None:
  tool = compile_project_agents(
    [], ToolOutputPolicy(), _dynamic_definition(max_task_chars=5)
  ).tools[0]
  runner = Runner(checkpoint="memory", memory="in_memory")
  parent = await runner._create_session(Agent(model="parent", tools=[]), deps=None)

  result = await tool.invoke(RunContext(parent), {"role": "reviewer", "task": "too long"})

  assert result["error_type"] == "invalid_arguments"
  assert parent.extension_state.get("project_agents", {}) == {}


def test_invalid_dynamic_policy_disables_all_agent_tools() -> None:
  compiled = compile_project_agents(
    [_definition()],
    ToolOutputPolicy(),
    _dynamic_definition(model="", max_turns=20),
  )

  assert compiled.errors
  assert compiled.tools == []


def test_dynamic_agent_rejects_static_spawn_name_collision() -> None:
  compiled = compile_project_agents(
    [_definition(name="spawn")],
    ToolOutputPolicy(),
    _dynamic_definition(),
  )

  assert compiled.errors
  assert compiled.tools == []
  assert "collides" in compiled.errors[0].message
