"""Tests for AgentFactory external-tool support."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from nonoka import SkillRegistry
from nonoka.core.errors import ExternalToolExecutionRequiredError

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory


@pytest.fixture
def factory():
  config = CLIConfig(model="gpt-4o")
  return AgentFactory(config)


@pytest.mark.asyncio
async def test_build_injects_execution_plan(factory):
  agent = factory.build(execution_plan="1. Read foo.py\n2. Edit foo.py")
  assert "## Execution Plan" in agent.system_prompt
  assert "1. Read foo.py" in agent.system_prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_injects_execution_plan(factory):
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(
    tools,
    execution_plan="1. Read foo.py\n2. Edit foo.py",
  )
  assert "## Execution Plan" in agent.system_prompt
  assert "1. Read foo.py" in agent.system_prompt


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
  assert cap.execution.mutates_workspace is False
  assert cap.execution.stateful_action is True
  assert cap.requires_workspace_attestation is True
  schema = cap.to_json_schema()
  assert schema["function"]["name"] == "bash"
  assert "command" in schema["function"]["parameters"]["properties"]


def test_known_read_only_external_tools_do_not_require_workspace_attestation():
  cap = AgentFactory.create_external_tool_capability(
    name="read",
    description="Read a file",
    parameters={"type": "object", "properties": {}},
  )
  assert cap.execution.read_only is True
  assert cap.execution.parallel_safe is True
  assert cap.requires_workspace_attestation is False

  unknown = AgentFactory.create_external_tool_capability(
    name="vendor_action",
    description="Run an unknown host action",
    parameters={"type": "object", "properties": {}},
  )
  assert unknown.execution.read_only is False
  assert unknown.requires_workspace_attestation is True


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
  assert {tool.name for tool in agent.tools} == {"bash", "nonoka__search_evidence"}
  assert "OpenCode" in agent.system_prompt
  assert "todowrite" not in agent.system_prompt
  assert "execute_command" not in agent.system_prompt


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
  assert "Use only these exact host tool names: `bash`" in agent.system_prompt
  assert "Do not substitute CLI aliases" in agent.system_prompt
  assert "Preserve volatile evidence" in agent.system_prompt
  assert "Treat an unambiguous task instruction as authorization" in agent.system_prompt
  assert "do not finish with an audit or plan" in agent.system_prompt
  assert "Before completion, verify every requested output" in agent.system_prompt
  assert "NONOKA_VERIFY=focused" in agent.system_prompt


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
  assert "Internal MCP tools (nonoka executes" in prompt
  assert "Use the EXACT tool names below" in prompt
  assert "Nonoka-managed bridge tools (nonoka executes)" in prompt
  assert "`nonoka__search_evidence`" in prompt
  # Colon-prefixed names are not passed to the LLM.
  assert "mcp:filesystem:list_directory" not in prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_accepts_external_mcp_servers():
  from nonoka_cli.bridge.protocol import (
    ExternalMCPServerDefinition,
    ExternalMCPToolDefinition,
  )

  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  external_mcp = [
    ExternalMCPServerDefinition(
      name="filesystem",
      description="File access",
      tools=[
        ExternalMCPToolDefinition(
          name="list_directory",
          description="List files",
          parameters={"type": "object", "properties": {}},
        )
      ],
    )
  ]
  agent = factory.build_with_external_tools(
    tools,
    external_mcp_servers=external_mcp,
  )
  tool_names = {t.name for t in agent.tools}
  assert "bash" in tool_names
  assert "mcp__filesystem__list_directory" in tool_names

  cap = next(t for t in agent.tools if t.name == "mcp__filesystem__list_directory")
  assert cap.external is True
  assert cap.metadata == {
    "kind": "mcp_tool",
    "server": "filesystem",
    "original_name": "list_directory",
  }

  prompt = agent.system_prompt
  assert "External MCP tools (host executes" in prompt
  assert "`mcp__filesystem__list_directory`" in prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_accepts_external_skills():
  from nonoka_cli.bridge.protocol import (
    ExternalSkillDefinition,
    ExternalSkillToolDefinition,
  )

  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  external_skills = [
    ExternalSkillDefinition(
      name="code-review",
      description="Review code",
      tools=[
        ExternalSkillToolDefinition(
          name="review_file",
          description="Review a file",
          parameters={"type": "object", "properties": {}},
        )
      ],
      system_prompt="You are a reviewer.",
      activation_prompt="Review carefully.",
    )
  ]
  agent = factory.build_with_external_tools(
    tools,
    external_skills=external_skills,
  )
  tool_names = {t.name for t in agent.tools}
  assert "bash" in tool_names
  assert "skill__code-review__review_file" in tool_names

  cap = next(t for t in agent.tools if t.name == "skill__code-review__review_file")
  assert cap.external is True
  assert cap.metadata == {
    "kind": "skill_tool",
    "skill": "code-review",
    "original_name": "review_file",
  }

  prompt = agent.system_prompt
  assert "External skill tools (host executes" in prompt
  assert "`skill__code-review__review_file`" in prompt


def test_is_opencode_native_skill_enabled(tmp_path: Path):
  # Missing cwd and missing file -> treated as safe (disabled / not OpenCode).
  assert AgentFactory._is_opencode_native_skill_enabled(None) is False
  assert AgentFactory._is_opencode_native_skill_enabled(tmp_path) is False

  # Explicitly disabled.
  (tmp_path / "opencode.json").write_text(json.dumps({"tools": {"skill": False}}))
  assert AgentFactory._is_opencode_native_skill_enabled(tmp_path) is False

  # Explicitly enabled.
  (tmp_path / "opencode.json").write_text(json.dumps({"tools": {"skill": True}}))
  assert AgentFactory._is_opencode_native_skill_enabled(tmp_path) is True

  # Default when tools key exists but skill is omitted.
  (tmp_path / "opencode.json").write_text(json.dumps({"tools": {}}))
  assert AgentFactory._is_opencode_native_skill_enabled(tmp_path) is True


@pytest.mark.asyncio
async def test_build_with_external_tools_warns_when_native_skill_enabled(tmp_path: Path):
  (tmp_path / "opencode.json").write_text(json.dumps({"tools": {"skill": True}}))

  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools, cwd=str(tmp_path))
  prompt = agent.system_prompt
  assert "OpenCode's native skill tool is enabled" in prompt
  assert "skill:<name>" in prompt
  assert "skill__<skill>__<tool>" in prompt


@pytest.mark.asyncio
async def test_build_with_external_tools_no_warning_when_native_skill_disabled(tmp_path: Path):
  (tmp_path / "opencode.json").write_text(json.dumps({"tools": {"skill": False}}))

  config = CLIConfig(model="gpt-4o")
  factory = AgentFactory(config)
  tools = [
    AgentFactory.create_external_tool_capability(
      name="bash",
      description="Run shell commands",
      parameters={"type": "object", "properties": {}},
    )
  ]
  agent = factory.build_with_external_tools(tools, cwd=str(tmp_path))
  prompt = agent.system_prompt
  assert "OpenCode's native skill tool is enabled" not in prompt


def test_generation_options_attach_persisted_runtime_and_completion_contract():
  factory = AgentFactory(CLIConfig(model="gpt-4o"))
  factory.set_generation_options(
    max_turns=12,
    timeout_seconds=30,
    wall_timeout_seconds=600,
    tool_budget=40,
    max_context_bytes=262144,
    max_external_result_bytes=65536,
    require_workspace_mutation=True,
    max_completion_corrections=3,
  )
  agent = factory.build()
  assert agent.runtime_limits.max_model_turns == 12
  assert agent.runtime_limits.max_tool_calls == 40
  assert agent.runtime_limits.model_timeout_seconds == 30
  assert agent.runtime_limits.wall_timeout_seconds == 600
  assert agent.runtime_limits.max_context_bytes == 262144
  assert agent.runtime_limits.max_external_result_bytes == 65536
  assert agent.completion_contract.require_workspace_mutation is True
  assert agent.completion_contract.require_complete_observations is True
  assert agent.completion_contract.max_corrections == 3
  assert agent.completion_contract.enforcement == "strict"
  assert [extension.name for extension in agent.extensions] == ["workspace_progress"]


def test_factory_propagates_hard_token_and_cost_budgets():
  config = CLIConfig.model_validate(
    {
      "model": "gpt-4o",
      "budget": {"max_total_tokens": 4000, "max_cost_usd": 0.25, "fail_on_unknown_cost": True},
    }
  )
  agent = AgentFactory(config).build()

  assert agent.runtime_limits.max_total_tokens == 4000
  assert agent.runtime_limits.max_cost_usd == 0.25
  assert agent.runtime_limits.fail_on_unknown_cost is True


def test_generation_options_can_explicitly_disable_cumulative_budgets():
  factory = AgentFactory(CLIConfig(model="gpt-4o"))
  factory.set_generation_options(
    max_turns=None,
    wall_timeout_seconds=3600,
    tool_budget=None,
  )

  agent = factory.build()

  assert agent.max_turns is None
  assert agent.max_steps is None
  assert agent.runtime_limits.max_model_turns is None
  assert agent.runtime_limits.max_tool_calls is None
  assert agent.runtime_limits.wall_timeout_seconds == 3600


def test_generation_options_can_require_observed_effect_without_workspace_mutation():
  factory = AgentFactory(CLIConfig(model="gpt-4o"))
  factory.set_generation_options(require_observed_effect=True)

  agent = factory.build()

  assert agent.completion_contract.require_observed_effect is True
  assert agent.completion_contract.require_workspace_mutation is False
  assert [extension.name for extension in agent.extensions] == ["workspace_progress"]


def test_generation_options_can_require_focused_verification_with_advisory_scoring():
  factory = AgentFactory(CLIConfig(model="gpt-4o"))
  factory.set_generation_options(
    require_observed_effect=True,
    require_focused_verification=True,
    verification_enforcement="advisory",
  )

  agent = factory.build()

  assert agent.completion_contract.require_focused_verification is True
  assert agent.completion_contract.enforcement == "advisory"


def test_title_agent_has_no_tools_or_completion_contract():
  factory = AgentFactory(CLIConfig(model="gpt-4o"))
  factory.set_generation_options(require_workspace_mutation=True)
  agent = factory.build_title_agent()
  assert agent.tools == []
  assert agent.max_turns == 1
  assert agent.completion_contract is None
  assert "Do not call tools" in agent.system_prompt
