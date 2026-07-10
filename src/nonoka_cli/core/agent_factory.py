"""Agent factory — builds nonoka Agent from CLI configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from nonoka import Agent, AgentBuilder
from nonoka.core.context import RunContext
from nonoka.core.types import Capability

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.skills.manager import SkillManager
from nonoka_cli.tools.loader import ToolLoader
from nonoka_cli.utils.errors import AgentBuildError

logger = structlog.get_logger("nonoka_cli.core")


_DEFAULT_CODING_SYSTEM_PROMPT = """\
You are nonoka-cli, an autonomous ai assistant running in a terminal.
You can read, write, edit, and delete files, run shell commands, and explore
the project directory. Use your tools proactively to complete tasks.

When given a task, follow this workflow:

1. Understand the goal. Ask clarifying questions only when the request is ambiguous.
2. Explore the working directory with `list_dir`, `view_dir`, and `grep_files`
   to understand the project structure.
3. Read relevant files with `view` or `read_file` before modifying them.
4. Make changes with the right tool:
   - `write_file` for new files or complete rewrites.
   - `edit_file` for small, precise changes (old_string must appear exactly once).
   - `search_and_replace` for bulk replacements.
   - `delete_file` to remove files.
5. Run commands with `execute_command` to install dependencies, build, test,
   lint, or verify your work.
6. Report what you did, what was created/changed, and any remaining manual steps.

Guidelines:
- Always read a file before editing it.
- When using `edit_file`, include enough surrounding context in `old_string`
  so the match is unique.
- Prefer `write_file` over many small `edit_file` calls when rewriting a whole
  file or large section.
- Run build/test commands when available and report the result.
- If a command fails, analyze the output and try to fix the issue.
- Keep responses concise but thorough.

You operate in the user's current working directory.
"""

_OPENCODE_HOSTED_SYSTEM_PROMPT = """\
You are an autonomous coding assistant running inside OpenCode.
Use only the tools provided by OpenCode to complete tasks.
OpenCode handles all tool execution and any required approvals.

MANDATORY TODO WORKFLOW for multi-step tasks:
1. FIRST, call the `todowrite` tool to create the complete todo list. Mark the
   first step as `in_progress` and the rest as `pending`.
2. BEFORE starting any step, call `todowrite` to mark that step as
   `in_progress` and any previously completed steps as `completed`.
3. AFTER a step finishes successfully, call `todowrite` to mark it as
   `completed` and the next step as `in_progress`.
4. If a step fails or is skipped, call `todowrite` to mark it as `cancelled`.
5. At the end, call `todowrite` with every item marked `completed`.

Do not skip these updates. The user sees progress through the OpenCode TODO
UI, so you must keep the list accurate at every transition.

Use these statuses exactly:
- `pending` for steps not yet started.
- `in_progress` for the step currently being worked on.
- `completed` for steps that finished successfully.
- `cancelled` for steps that failed or were skipped.

Guidelines:
- Prefer reading files before editing them.
- Run build/test commands when available and report the result.
- If a command fails, analyze the output and try to fix the issue.
- Keep responses concise but thorough.

You operate in the user's current working directory.
"""


class AgentFactory:
  """Builds nonoka Agent instances from CLI configuration.

  Supports integrating tools from:
  - Built-in tools (always available)
  - Local tool directories (via ``ToolLoader``)
  - MCP servers (via ``MCPManager``)
  - Skills (via ``SkillManager``)
  """

  def __init__(
    self,
    config: CLIConfig,
    mcp_manager: MCPManager | None = None,
    tool_loader: ToolLoader | None = None,
    skill_manager: SkillManager | None = None,
  ):
    """Args:
      config: Validated CLI configuration.
      mcp_manager: Optional MCP manager whose discovered tools are registered
        with the Agent.
      tool_loader: Optional tool loader for local / built-in tools.
      skill_manager: Optional skill manager for applying configured skills.
    """
    self._config = config
    self._mcp_manager = mcp_manager
    self._tool_loader = tool_loader
    self._skill_manager = skill_manager
    self._agent: Agent | None = None

  @property
  def config(self) -> CLIConfig:
    """Current configuration."""
    return self._config

  @property
  def tool_loader(self) -> ToolLoader | None:
    """Current tool loader, if any."""
    return self._tool_loader

  @property
  def skill_manager(self) -> SkillManager | None:
    """Current skill manager, if any."""
    return self._skill_manager

  def build(self) -> Agent:
    """Build (or rebuild) an Agent from the current configuration.

    Injects the current model identifier into the system prompt so the
    model can accurately answer questions about its own identity.

    Returns:
      A nonoka Agent instance.

    Raises:
      AgentBuildError: If model is not configured.
    """
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")

    system_prompt = self._build_system_prompt()

    logger.info(
      "building_agent",
      model=self._config.model,
      system_prompt_length=len(system_prompt),
    )

    builder = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(system_prompt)
      .max_turns(20)
    )

    # Local / built-in tools via ToolRegistry so runtime reloads are reflected
    # without rebuilding the Agent.
    if self._tool_loader is not None:
      registry = self._tool_loader.load_all()
      builder = builder.tool_registry(registry)
      logger.debug("agent_factory_local_tools", count=len(registry))

    # MCP tools are individual Capabilities managed by MCPManager.
    if self._mcp_manager is not None:
      mcp_tools = self._mcp_manager.get_tools()
      if mcp_tools:
        builder = builder.tools(*mcp_tools)
        logger.debug("agent_factory_mcp_tools", count=len(mcp_tools))

    # Apply configured skills declaratively through the builder.
    if self._skill_manager is not None and self._config.skills:
      skills = self._skill_manager.load_all(self._config.skills)
      if skills:
        builder = builder.skills(*skills)
        logger.info(
          "skills_applied",
          skills=self._config.skills,
        )

    self._agent = builder.build()
    return self._agent

  def _build_system_prompt(self) -> str:
    """Build the effective system prompt, injecting the current model name."""
    base = self._config.system_prompt or _DEFAULT_CODING_SYSTEM_PROMPT
    model = self._config.model.strip()

    # Avoid injecting the identity line twice if the user already wrote one.
    if f"Your current model is: {model}" in base:
      return base

    identity_line = f"\n\nYour current model is: {model}."
    return base.rstrip() + identity_line

  def build_with_external_tools(
    self,
    tools: list[Capability],
    cwd: str | Path | None = None,
    host_system_prompt: str | None = None,
  ) -> Agent:
    """Build a temporary Agent that uses only externally-supplied tools.

    This is used when nonoka-cli is hosted inside OpenCode: OpenCode sends
    its native tool definitions, nonoka registers them as opaque capabilities,
    and OpenCode executes the actual tool calls. Local tools, MCP tools, and
    skills are intentionally excluded.

    Args:
      tools: Tool definitions supplied by the OpenCode host.
      cwd: Current working directory to inject into the system prompt.
      host_system_prompt: Optional system prompt forwarded by the host
        (e.g. OpenCode's agent file). Used only when the user has not
        configured a custom ``system_prompt`` in nonoka.yaml.
    """
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")

    system_prompt = self._build_external_system_prompt(
      base=self._config.system_prompt or host_system_prompt or _OPENCODE_HOSTED_SYSTEM_PROMPT,
      cwd=cwd,
    )

    logger.info(
      "building_agent_with_external_tools",
      model=self._config.model,
      tool_count=len(tools),
      system_prompt_length=len(system_prompt),
      cwd=str(cwd) if cwd else None,
    )

    builder = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(system_prompt)
      .max_turns(20)
      .tools(*tools)
    )

    return builder.build()

  def _build_external_system_prompt(
    self,
    base: str,
    cwd: str | Path | None,
  ) -> str:
    """Assemble the system prompt for the OpenCode-hosted agent."""
    model = self._config.model.strip()

    # Inject model identity once.
    identity_line = f"Your current model is: {model}."
    if identity_line not in base:
      base = base.rstrip() + f"\n\n{identity_line}"

    # Inject cwd and path guidance when a cwd is provided.
    if cwd is not None:
      cwd_str = str(Path(cwd).resolve())
      cwd_block = (
        f"\n\nCurrent working directory: {cwd_str}\n"
        "All file paths must be relative to this directory or use the absolute path above.\n"
        "Prefer write_file/edit_file over bash/execute_command for file mutations."
      )
      if "Current working directory:" not in base:
        base = base.rstrip() + cwd_block

    return base

  @staticmethod
  def create_external_tool_capability(
    name: str,
    description: str,
    parameters: dict[str, Any],
  ) -> Capability:
    """Create a lightweight Capability from an OpenCode tool definition.

    The returned capability carries the JSON schema the LLM sees. Its
    ``invoke`` method must never be called: execution is delegated to OpenCode
    via the ``external=True`` marker.
    """
    return _ExternalCapability(
      name=name,
      description=description,
      parameters=parameters,
    )

  def _collect_tools(self) -> list[Capability]:
    """Collect tools from all configured sources for listing/inspection."""
    tools: list[Capability] = []

    # Local / built-in tools
    if self._tool_loader is not None:
      registry = self._tool_loader.load_all()
      tools.extend(registry.get_all())
      logger.debug("agent_factory_local_tools", count=len(registry))

    # MCP tools
    if self._mcp_manager is not None:
      mcp_tools = self._mcp_manager.get_tools()
      tools.extend(mcp_tools)
      logger.debug("agent_factory_mcp_tools", count=len(mcp_tools))

    return tools

  def list_all_tools(self) -> list[Capability]:
    """Return all tools that the current Agent would be built with.

    Loads tools without building the Agent, so it can be used for ``/tool
    list`` even when the Agent has not been materialized yet.
    """
    return self._collect_tools()

  def get_tool(self, name: str) -> Capability | None:
    """Find a tool by name across all configured sources."""
    for tool in self.list_all_tools():
      if tool.name == name:
        return tool
    return None

  def rebuild(self, config_patch: dict[str, Any] | None = None) -> Agent:
    """Rebuild Agent with an optional configuration patch.

    Args:
      config_patch: Dict of config overrides (e.g. {"model": "gpt-4o"}).

    Returns:
      The rebuilt Agent.
    """
    if config_patch:
      # Apply patch by creating a new config
      data = self._config.model_dump()
      data.update(config_patch)
      self._config = self._config.__class__.model_validate(data)

    return self.build()

  def get_agent(self) -> Agent | None:
    """Return the currently built Agent, if any."""
    return self._agent


@dataclass
class _ExternalCapability:
  """A capability whose execution is delegated to an external host.

  Implements the ``nonoka.core.types.Capability`` protocol enough for the
  nonoka Agent to register the tool schema and emit a tool call. The
  ``external=True`` marker tells ``ReActAgent`` to pause and let the host
  execute the tool instead of calling ``invoke``.
  """

  name: str
  description: str
  parameters: dict[str, Any]
  external: bool = field(default=True, init=False)

  async def invoke(self, ctx: RunContext, arguments: dict[str, Any]) -> Any:
    raise RuntimeError(
      f"External tool '{self.name}' must be executed by the host, not nonoka."
    )

  def to_json_schema(self) -> dict[str, Any]:
    return {
      "type": "function",
      "function": {
        "name": self.name,
        "description": self.description,
        "parameters": self.parameters,
      },
    }
