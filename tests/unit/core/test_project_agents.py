from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from nonoka import Agent, ExternalCapability, Runner
from nonoka.core.context import RunContext

from nonoka_cli.core.plugin_manifest import AgentEntry
from nonoka_cli.core.project_agents import (
  ProjectAgentDefinition,
  compile_project_agents,
)
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy


class _LocalCapability:
  """Minimal in-process capability standing in for a parent read-only tool."""

  name = "read_file"
  description = "Read a file."
  parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
  external = False

  async def invoke(self, ctx: RunContext, arguments: dict[str, Any]) -> Any:
    return "file content"

  def to_json_schema(self) -> dict[str, Any]:
    return {
      "type": "function",
      "function": {
        "name": self.name,
        "description": self.description,
        "parameters": self.parameters,
      },
    }


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


def _make_tool(**overrides):
  return compile_project_agents([_definition(**overrides)], ToolOutputPolicy()).tools[0]


async def _invoke(tool, parent: Agent, arguments: dict[str, Any]) -> Any:
  runner = Runner(checkpoint="memory", memory="in_memory")
  provider = MagicMock()
  provider.chat = AsyncMock(
    return_value=MagicMock(content="bounded plan", tool_calls=None, usage={})
  )
  runner._create_llm = lambda agent: provider  # type: ignore[method-assign]
  runner.llm = provider
  session = await runner._create_session(parent, deps=None)
  result = await tool.invoke(RunContext(session), arguments)
  return session, result


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
  # Model-slot races are fixed in the framework (Runner.current_llm), so
  # read-only child agents may run in the scheduler's parallel wave.
  assert tool.execution.read_only is True
  assert tool.execution.parallel_safe is True
  assert tool.metadata["source"].endswith(".nonoka/plugin.json")


def test_compile_errors_disable_all_project_agents() -> None:
  compiled = compile_project_agents(
    [_definition(), _definition(name="bad role", model="")],
    ToolOutputPolicy(),
  )

  assert compiled.errors
  assert compiled.tools == []


def test_allowed_tools_empty_keeps_child_tool_free_and_says_so() -> None:
  tool = _make_tool(description="")
  assert list(tool.agent.tools) == []
  assert tool.agent.max_steps == 0
  assert "tool-free" in tool.description


def test_allowed_tools_description_lists_declared_tools() -> None:
  tool = _make_tool(description="", allowed_tools=["read_file", "grep"])
  assert "read_file" in tool.description
  assert "grep" in tool.description
  assert tool.agent.max_steps == 8  # default tool_budget


def test_allowed_tools_with_custom_tool_budget() -> None:
  tool = _make_tool(allowed_tools=["read_file"], tool_budget=3)
  assert tool.agent.max_steps == 3


def test_invalid_allowed_tools_or_tool_budget_disable_role() -> None:
  bad_name = compile_project_agents(
    [_definition(allowed_tools=["bad name!"])], ToolOutputPolicy(),
  )
  assert bad_name.errors

  bad_budget = compile_project_agents(
    [_definition(allowed_tools=["read_file"], tool_budget=0)], ToolOutputPolicy(),
  )
  assert bad_budget.errors


@pytest.mark.asyncio
async def test_allowed_tools_grants_parent_tools_to_child() -> None:
  tool = _make_tool(allowed_tools=["read_file"])
  parent = Agent(model="parent", tools=[_LocalCapability()])

  _session, result = await _invoke(tool, parent, {"task": "inspect"})

  assert result["success"] is True
  assert [cap.name for cap in tool.agent.tools] == ["read_file"]
  assert tool.agent.max_steps == 8


@pytest.mark.asyncio
async def test_allowed_tools_unknown_names_warn_without_failing() -> None:
  tool = _make_tool(allowed_tools=["read_file", "nope"])
  parent = Agent(model="parent", tools=[_LocalCapability()])

  _session, result = await _invoke(tool, parent, {"task": "inspect"})

  assert result["success"] is True
  assert result["unknown_allowed_tools"] == ["nope"]
  assert [cap.name for cap in tool.agent.tools] == ["read_file"]


@pytest.mark.asyncio
async def test_allowed_tools_external_capability_degrades_with_structured_error() -> None:
  """Host-native tools pause the issuing session, but the bridge only resumes
  the top-level session, so a child can never receive their results."""
  tool = _make_tool(allowed_tools=["read"])
  host_tool = ExternalCapability(
    name="read",
    description="Host-native read.",
    parameters={"type": "object", "properties": {}, "required": []},
  )
  parent = Agent(model="parent", tools=[host_tool])

  session, result = await _invoke(tool, parent, {"task": "inspect"})

  assert result["success"] is False
  assert result["error_type"] == "unsupported_sub_agent_tools"
  assert "read" in result["error"]
  # The degraded call must not spend an invocation.
  assert session.extension_state.get("project_agents", {}) == {}


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
