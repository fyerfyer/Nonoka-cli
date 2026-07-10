"""Tests for AgentFactory external-tool support."""

from __future__ import annotations

import pytest

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory, _ExternalCapability


@pytest.fixture
def factory():
  config = CLIConfig(model="gpt-4o")
  return AgentFactory(config)


def test_create_external_tool_capability():
  cap = AgentFactory.create_external_tool_capability(
    name="bash",
    description="Run shell commands",
    parameters={
      "type": "object",
      "properties": {"command": {"type": "string"}},
      "required": ["command"],
    },
  )
  assert cap.name == "bash"
  assert cap.description == "Run shell commands"
  assert cap.external is True
  schema = cap.to_json_schema()
  assert schema["function"]["name"] == "bash"
  assert "command" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_external_capability_invoke_raises(factory):
  cap = AgentFactory.create_external_tool_capability(
    name="bash",
    description="Run shell commands",
    parameters={"type": "object", "properties": {}},
  )
  with pytest.raises(RuntimeError, match="External tool 'bash' must be executed by the host"):
    await cap.invoke(None, {"command": "ls"})


@pytest.mark.asyncio
async def test_build_with_external_tools(factory):
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {"command": {"type": "string"}}},
    )
  ]
  agent = factory.build_with_external_tools(tools)
  assert agent.model == "gpt-4o"
  assert len(agent.tools) == 1
  assert agent.tools[0].name == "bash"
  assert "OpenCode" in agent.system_prompt
  assert "todowrite" in agent.system_prompt
  assert "in_progress" in agent.system_prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_uses_config_system_prompt():
  config = CLIConfig(model="gpt-4o", system_prompt="Custom OpenCode prompt.")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools)
  assert agent.system_prompt.startswith("Custom OpenCode prompt.")
  assert "Your current model is: gpt-4o" in agent.system_prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_injects_cwd():
  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools, cwd="/tmp/workspace")
  assert "Current working directory: /tmp/workspace" in agent.system_prompt
  assert "Prefer write_file/edit_file over bash/execute_command" in agent.system_prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_uses_host_system_prompt_fallback():
  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(
    tools,
    cwd="/tmp/workspace",
    host_system_prompt="Host OpenCode prompt.",
  )
  assert agent.system_prompt.startswith("Host OpenCode prompt.")
  assert "Current working directory: /tmp/workspace" in agent.system_prompt
