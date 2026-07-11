"""Tests for AgentFactory external-tool support."""

from __future__ import annotations

import re

import pytest
from nonoka import SkillRegistry
from nonoka.core.errors import ExternalToolExecutionRequiredError

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory


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
  with pytest.raises(ExternalToolExecutionRequiredError):
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


class _FakeMCPCapability:
  """Minimal capability stand-in for MCP tool discovery tests."""

  name = "list_directory"

  async def invoke(self, ctx, arguments):
    return {}

  def to_json_schema(self):
    return {
      "type": "function",
      "function": {"name": self.name, "description": "Lists files."},
    }


class _FakeMCPManager:
  def get_tools(self):
    return [("filesystem", _FakeMCPCapability())]


@pytest.mark.asyncio
async def test_build_with_external_tools_includes_skills(tmp_path):
  skill_dir = tmp_path / ".nonoka" / "skills"
  skill_dir.mkdir(parents=True)
  skill_file = skill_dir / "code-review.md"
  skill_file.write_text(
    "---\n"
    "name: code-review\n"
    "description: Review code changes.\n"
    "tools: []\n"
    "---\n"
    "Review the code carefully.\n"
  )

  config = CLIConfig(model="gpt-4o", skills=["code-review"])
  factory = AgentFactory(
    config,
    skill_registry=SkillRegistry(
      enabled=["code-review"],
      search_paths=[skill_dir],
    ),
  )
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools)
  assert any(t.name == "bash" for t in agent.tools)
  assert "code-review" in agent.system_prompt
  assert "Review code changes" in agent.system_prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_prefixes_mcp_tools():
  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config, mcp_manager=_FakeMCPManager())
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools)
  tool_names = {t.name for t in agent.tools}
  assert "bash" in tool_names
  assert "mcp__filesystem__list_directory" in tool_names
  # Sanitized names must be provider-safe.
  assert all(re.match(r"^[a-zA-Z0-9_-]+$", n) for n in tool_names)


@pytest.mark.asyncio
async def test_build_with_external_tools_injects_namespace_block(tmp_path):
  skill_dir = tmp_path / ".nonoka" / "skills"
  skill_dir.mkdir(parents=True)
  (skill_dir / "code-review.md").write_text(
    "---\n"
    "name: code-review\n"
    "description: Review code changes.\n"
    "tools: []\n"
    "---\n"
    "Review the code carefully.\n"
  )
  config = CLIConfig(model="gpt-4o", skills=["code-review"])
  factory = AgentFactory(
    config,
    mcp_manager=_FakeMCPManager(),
    skill_registry=SkillRegistry(
      enabled=["code-review"],
      search_paths=[skill_dir],
    ),
  )
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools)
  prompt = agent.system_prompt
  assert "## Tool Namespaces" in prompt
  assert "`bash`" in prompt
  assert "`mcp__filesystem__list_directory`" in prompt
  assert "MCP tools (call as" in prompt
  assert "Use the EXACT tool names below" in prompt
  # Colon-prefixed names are not passed to the LLM.
  assert "mcp:filesystem:list_directory" not in prompt
