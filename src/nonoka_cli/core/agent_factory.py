"""Agent factory — builds nonoka Agent instances from CLI configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import structlog
from nonoka import (
  Agent,
  AgentBuilder,
  ExternalCapability,
  ExternalMCPRegistry,
  ExternalMCPServer,
  ExternalMCPToolDefinition,
  ExternalSkill,
  ExternalSkillRegistry,
  ExternalSkillToolDefinition,
  SkillRegistry,
  load_skill,
)
from nonoka.core.types import Capability

try:
  from nonoka.core.execution import ToolExecution
except ImportError:  # nonoka<=1.3.5; retain conservative metadata locally.

  @dataclass(frozen=True)
  class ToolExecution:  # type: ignore[no-redef]
    read_only: bool = False
    mutates_workspace: bool = False
    exclusive: bool = False
    stateful_action: bool = False
    pagination: bool = False

    @property
    def parallel_safe(self) -> bool:
      return self.read_only and not self.mutates_workspace and not self.stateful_action

  _SUPPORTS_EXECUTION_METADATA = False
else:
  _SUPPORTS_EXECUTION_METADATA = True

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.namespaces import (
  PrefixedCapability,
  mcp_tool_name,
  skill_tool_name,
)
from nonoka_cli.core.prompt_builder import SystemPromptBuilder
from nonoka_cli.mcp.manager import MCPManager
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

_OPENCODE_HOSTED_SYSTEM_PROMPT = (
  "You are nonoka-cli, an autonomous coding assistant running inside OpenCode.\n"
  "Use only the tools provided to you by this environment to complete tasks.\n"
  "OpenCode handles host native tool execution and any required approvals.\n\n"
  "Skill and MCP namespace rules (important):\n"
  "- nonoka-managed skills expose tools as ``skill__<skill>__<tool>``.\n"
  "- nonoka-managed MCP servers expose tools as ``mcp__<server>__<tool>``.\n"
  "- To activate a skill's full guidance, call the ``load_skill`` tool first.\n"
  "- Do NOT use OpenCode's native ``skill:<name>`` syntax; it is disabled in\n"
  "  this configuration to avoid conflicting with nonoka's skill tools.\n\n"
  "Guidelines:\n"
  "- Prefer reading files before editing them.\n"
  "- Run build/test commands when available and report the result.\n"
  "- If a command fails, analyze the output and try to fix the issue.\n"
  "- Keep responses concise but thorough.\n\n"
  "You operate in the user's current working directory.\n"
)


class AgentFactory:
  """Builds nonoka Agent instances from CLI configuration.

  Supports integrating tools from:
  - Built-in tools (always available)
  - Local tool directories (via ``ToolLoader``)
  - MCP servers (via ``MCPManager``)
  - Skills (via nonoka-agent ``SkillRegistry`` + ``load_skill``)
  """

  def __init__(
    self,
    config: CLIConfig,
    mcp_manager: MCPManager | None = None,
    tool_loader: ToolLoader | None = None,
    skill_registry: SkillRegistry | None = None,
    allowed_tools: list[str] | None = None,
  ):
    """Args:
    config: Validated CLI configuration.
    mcp_manager: Optional MCP manager whose discovered tools are registered
     with the Agent.
    tool_loader: Optional tool loader for local / built-in tools.
    skill_registry: Optional pre-built skill registry. If omitted, the
     factory constructs one from ``config.skills`` at build time.
    allowed_tools: Optional whitelist of tool names that do not require
     human approval. Non-listed tools are marked as requiring approval.
    """
    self._config = config
    self._mcp_manager = mcp_manager
    self._tool_loader = tool_loader
    self._skill_registry = skill_registry
    self._allowed_tools = set(allowed_tools or [])
    self._agent: Agent | None = None
    self._repo_map: str | None = None
    self._git_summary: str | None = None
    self._plugin_summary: str | None = None
    self._execution_plan: str | None = None
    self._generation_max_turns: int | None = None
    self._generation_temperature: float | None = None
    self._generation_timeout_seconds: float | None = None
    self._generation_tool_budget: int | None = None

  @property
  def config(self) -> CLIConfig:
    """Current configuration."""
    return self._config

  @property
  def tool_loader(self) -> ToolLoader | None:
    """Current tool loader, if any."""
    return self._tool_loader

  @property
  def skill_registry(self) -> SkillRegistry | None:
    """Current skill registry, if any."""
    return self._skill_registry

  def _executor_max_turns(self) -> int:
    """Return the configured executor max turns, falling back to 20."""
    if self._generation_max_turns is not None:
      return self._generation_max_turns
    configured = getattr(
      self._config.agents.executor, "max_turns", None
    )
    return configured if configured else 20

  def set_generation_options(
    self,
    *,
    max_turns: int | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    tool_budget: int | None = None,
  ) -> None:
    """Apply per-run generation limits used by the OpenCode bridge.

    These settings are deliberately held outside the persisted CLI config so
    benchmark runs can be reproduced without mutating a user's configuration.
    ``max_steps`` is the framework's total tool-call budget.
    """
    self._generation_max_turns = max_turns
    self._generation_temperature = temperature
    self._generation_timeout_seconds = timeout_seconds
    self._generation_tool_budget = tool_budget

  def _apply_generation_options(self, builder: AgentBuilder) -> AgentBuilder:
    """Attach optional bridge generation settings with old-framework fallback."""
    if self._generation_tool_budget is not None and hasattr(builder, "max_steps"):
      builder = builder.max_steps(self._generation_tool_budget)
    if self._generation_timeout_seconds is not None and hasattr(builder, "timeout"):
      builder = builder.timeout(self._generation_timeout_seconds)
    return builder

  def _finalize_agent(self, agent: Agent) -> Agent:
    """Set temperature after builder construction across supported frameworks."""
    if self._generation_temperature is None:
      return agent
    try:
      return replace(agent, temperature=self._generation_temperature)
    except (AttributeError, TypeError):
      # Released framework versions predate generation metadata. They remain
      # usable, but cannot safely mutate a frozen Agent instance.
      logger.warning("generation_temperature_not_supported")
      return agent

  def build(
    self,
    *,
    repo_map: str | None = None,
    git_summary: str | None = None,
    plugin_summary: str | None = None,
    execution_plan: str | None = None,
  ) -> Agent:
    """Build (or rebuild) an Agent from the current configuration.

    Injects the current model identifier into the system prompt so the
    model can accurately answer questions about its own identity.

    Args:
     repo_map: Optional repo-map block to inject into the system prompt.
     git_summary: Optional git-checkpoint status summary.
     plugin_summary: Optional loaded plugin manifest summary.

    Returns:
     A nonoka Agent instance.

    Raises:
     AgentBuildError: If model is not configured.
    """
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")

    # Keep the most recently supplied context blocks so rebuilds can reuse them
    # when the caller does not pass fresh values.
    if repo_map is not None:
      self._repo_map = repo_map
    if git_summary is not None:
      self._git_summary = git_summary
    if plugin_summary is not None:
      self._plugin_summary = plugin_summary
    if execution_plan is not None:
      self._execution_plan = execution_plan

    system_prompt = self._build_system_prompt(
      repo_map=self._repo_map,
      git_summary=self._git_summary,
      plugin_summary=self._plugin_summary,
      execution_plan=self._execution_plan,
    )

    logger.info(
      "building_agent",
      model=self._config.model,
      system_prompt_length=len(system_prompt),
    )

    builder = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(system_prompt)
      .max_turns(self._executor_max_turns())
    )

    # Local / built-in tools via ToolRegistry so runtime reloads are reflected
    # without rebuilding the Agent.
    if self._tool_loader is not None:
      registry = self._tool_loader.load_all()
      builder = builder.tool_registry(registry)
      logger.debug("agent_factory_local_tools", count=len(registry))

    # Lazy-load skill registry: only names/descriptions are injected eagerly;
    # full guidance is loaded on-demand via the ``load_skill`` tool.
    skill_registry = self._skill_registry_for_build()
    if skill_registry is not None:
      builder = builder.skill_manager(skill_registry).tool(load_skill)
      logger.debug(
        "agent_factory_skills",
        enabled=self._config.skills,
      )

    # MCP tools are individual Capabilities managed by MCPManager.
    if self._mcp_manager is not None:
      mcp_tools = self._mcp_manager.get_tools()
      if mcp_tools:
        for _, capability in mcp_tools:
          builder = builder.tool(capability)
        logger.debug("agent_factory_mcp_tools", count=len(mcp_tools))

    self._agent = self._finalize_agent(self._apply_generation_options(builder).build())
    return self._agent

  def _build_system_prompt(
    self,
    *,
    repo_map: str | None = None,
    git_summary: str | None = None,
    plugin_summary: str | None = None,
    execution_plan: str | None = None,
  ) -> str:
    """Build the effective system prompt, injecting the current model name."""
    base = self._config.system_prompt or _DEFAULT_CODING_SYSTEM_PROMPT
    return SystemPromptBuilder(
      base=base,
      model=self._config.model,
      repo_map=repo_map,
      git_summary=git_summary,
      plugin_summary=plugin_summary,
      execution_plan=execution_plan,
      allowed_tools=list(self._allowed_tools),
    ).build()

  def build_with_external_tools(
    self,
    tools: list[Capability],
    cwd: str | Path | None = None,
    host_system_prompt: str | None = None,
    external_mcp_servers: list[Any] | None = None,
    external_skills: list[Any] | None = None,
    *,
    repo_map: str | None = None,
    git_summary: str | None = None,
    plugin_summary: str | None = None,
    execution_plan: str | None = None,
  ) -> Agent:
    """Build an Agent for host-managed (e.g. OpenCode) mode.

    The host sends its native tool definitions; nonoka registers them as
    opaque external capabilities and the host executes the actual tool calls.
    Configured skills and MCP servers are merged in with namespace prefixes
    (``skill__<skill>__<tool>`` and ``mcp__<server>__<tool>``) so they remain
    available without colliding with host native tools.

    External host-managed MCP servers and skills can also be supplied; their
    tool schemas are registered as external capabilities whose execution is
    delegated back to the host.

    Args:
     tools: Tool definitions supplied by the host.
     cwd: Current working directory to inject into the system prompt.
     host_system_prompt: Optional system prompt forwarded by the host.
      Used only when the user has not configured a custom ``system_prompt``
      in nonoka.yaml.
     external_mcp_servers: Optional host-managed MCP server definitions.
     external_skills: Optional host-managed skill definitions.
    """
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")

    # Keep the most recently supplied context blocks so rebuilds can reuse them.
    if repo_map is not None:
      self._repo_map = repo_map
    if git_summary is not None:
      self._git_summary = git_summary
    if plugin_summary is not None:
      self._plugin_summary = plugin_summary
    if execution_plan is not None:
      self._execution_plan = execution_plan

    base = self._config.system_prompt or host_system_prompt or _OPENCODE_HOSTED_SYSTEM_PROMPT
    opencode_native_skill_enabled = self._is_opencode_native_skill_enabled(cwd)
    if opencode_native_skill_enabled:
      logger.warning("opencode_native_skill_enabled", cwd=str(cwd) if cwd else None)

    # 1. Host native tools (external capabilities).
    host_caps: list[Capability] = []
    for t in tools:
      if isinstance(t, Capability):
        host_caps.append(t)
      else:
        host_caps.append(
          self.create_external_tool_capability(
            name=t.name,
            description=t.description,
            parameters=t.parameters,
          )
        )

    host_tool_names = [c.name for c in host_caps]

    # 2. External host-managed MCP servers.
    external_mcp_registry: ExternalMCPRegistry | None = None
    external_mcp_tool_names: list[str] = []
    if external_mcp_servers:
      external_mcp_registry = ExternalMCPRegistry([
          ExternalMCPServer(
            name=s.name,
            description=s.description,
            tools=[
              ExternalMCPToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
              )
              for t in s.tools
            ],
          )
          for s in external_mcp_servers
      ])
      external_mcp_tool_names = [cap.name for cap in external_mcp_registry.get_tools()]

    # 3. External host-managed skills.
    external_skill_registry: ExternalSkillRegistry | None = None
    external_skill_tool_names: list[str] = []
    if external_skills:
      external_skill_registry = ExternalSkillRegistry([
          ExternalSkill(
            name=s.name,
            description=s.description,
            tools=[
              ExternalSkillToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
              )
              for t in s.tools
            ],
            system_prompt=s.system_prompt,
            activation_prompt=s.activation_prompt,
          )
          for s in external_skills
      ])
      external_skill_tool_names = [cap.name for cap in external_skill_registry.get_tools()]

    # 4. Internal skills (configured in nonoka.yaml).
    skill_registry = self._skill_registry_for_build(cwd)

    # 5. Internal MCP tools are prefixed with the server name to avoid collisions.
    mcp_tools: list[tuple[str, Capability]] = []
    mcp_tool_names: list[str] = []
    if self._mcp_manager is not None:
      mcp_tools = self._mcp_manager.get_tools()
      mcp_tool_names = [mcp_tool_name(server, cap.name) for server, cap in mcp_tools]

    # 6. Internal skill tools are prefixed with the skill name.
    skill_tool_names: list[str] = []
    if skill_registry is not None:
      for info in skill_registry.enabled:
        skill = skill_registry.get_skill(info.name)
        if skill is None:
          continue
        for tool in skill.tools:
          skill_tool_names.append(skill_tool_name(skill.name, tool.name))

    system_prompt = SystemPromptBuilder(
      base=base,
      model=self._config.model,
      cwd=cwd,
      host_tools=host_tool_names,
      external_mcp_tools=external_mcp_tool_names,
      external_skill_tools=external_skill_tool_names,
      internal_mcp_tools=mcp_tool_names,
      internal_skill_tools=skill_tool_names,
      opencode_native_skill_enabled=opencode_native_skill_enabled,
      repo_map=self._repo_map,
      git_summary=self._git_summary,
      plugin_summary=self._plugin_summary,
      execution_plan=self._execution_plan,
      allowed_tools=list(self._allowed_tools),
    ).build()

    logger.info(
      "building_agent_with_external_tools",
      model=self._config.model,
      host_tool_count=len(host_tool_names),
      external_mcp_tool_count=len(external_mcp_tool_names),
      external_skill_tool_count=len(external_skill_tool_names),
      internal_mcp_tool_count=len(mcp_tool_names),
      internal_skill_tool_count=len(skill_tool_names),
      system_prompt_length=len(system_prompt),
      cwd=str(cwd) if cwd else None,
    )

    builder = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(system_prompt)
      .max_turns(self._executor_max_turns())
    )

    # Register lazy-load internal skills with prefixed tool names.
    if skill_registry is not None:
      builder = builder.skill_manager(_PrefixedSkillRegistry(skill_registry)).tool(load_skill)

    # Register external skills (also lazy-loaded).
    if external_skill_registry is not None:
      builder = builder.external_skill_registry(external_skill_registry).tool(load_skill)

    # Register external MCP registry.
    if external_mcp_registry is not None:
      builder = builder.external_mcp_registry(external_mcp_registry)

    # Host native tools are external capabilities.
    for cap in host_caps:
      builder = builder.tool(cap)

    # Internal MCP tools are executed locally by nonoka-cli.
    for server, cap in mcp_tools:
      builder = builder.tool(PrefixedCapability(cap, f"mcp__{server}__"))

    self._agent = self._finalize_agent(self._apply_generation_options(builder).build())
    return self._agent

  def _skill_registry_for_build(
    self,
    cwd: str | Path | None = None,
  ) -> SkillRegistry | None:
    """Return a SkillRegistry for the current config and optional cwd."""
    if not self._config.skills:
      return None

    # If no cwd is supplied and a registry was injected, reuse it.
    if cwd is None and self._skill_registry is not None:
      return self._skill_registry

    search_paths: list[Path] = []
    if cwd is not None:
      cwd_path = Path(cwd).expanduser().resolve()
      search_paths.extend([
          cwd_path / ".nonoka" / "skills",
          cwd_path / "skills",
      ])

    return SkillRegistry(
      enabled=list(self._config.skills),
      search_paths=search_paths,
    )

  @staticmethod
  def create_external_tool_capability(
    name: str,
    description: str,
    parameters: dict[str, Any],
    metadata: dict[str, Any] | None = None,
  ) -> Capability:
    """Create a lightweight Capability from a host tool definition.

    The returned capability carries the JSON schema the LLM sees. Its
    ``invoke`` method must never be called: execution is delegated to the host
    via the ``external=True`` marker.
    """
    lowered = name.lower()
    read_only = lowered in {"read", "list", "glob", "grep", "search", "webfetch"}
    mutates_workspace = lowered in {
      "bash",
      "write",
      "edit",
      "delete",
      "apply_patch",
      "write_file",
      "edit_file",
      "delete_file",
      "execute_command",
    }
    execution = ToolExecution(
      read_only=read_only,
      mutates_workspace=mutates_workspace,
      stateful_action=not read_only,
    )
    kwargs: dict[str, Any] = {}
    if _SUPPORTS_EXECUTION_METADATA:
      kwargs["execution"] = execution
    capability = ExternalCapability(
      name=name,
      description=description,
      parameters=parameters,
      metadata=metadata or {"kind": "host_tool", "original_name": name},
      **kwargs,
    )
    if not _SUPPORTS_EXECUTION_METADATA:
      capability.execution = execution
      capability.requires_workspace_attestation = execution.mutates_workspace
    return capability

  @staticmethod
  def _is_opencode_native_skill_enabled(cwd: str | Path | None) -> bool:
    """Return True if cwd/opencode.json leaves OpenCode's native skill enabled.

    Defaults to False when the file is missing or unreadable, because this mode
    is only used with OpenCode and a missing config is treated as safe. Defaults
    to True when the file exists but does not explicitly disable ``tools.skill``,
    matching OpenCode's default behavior.
    """
    if cwd is None:
      return False
    path = Path(cwd) / "opencode.json"
    if not path.exists():
      return False
    try:
      data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
      return False
    tools = data.get("tools", {})
    if not isinstance(tools, dict):
      return True
    return tools.get("skill", True) is not False

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
      for _, cap in self._mcp_manager.get_tools():
        tools.append(cap)
      logger.debug("agent_factory_mcp_tools", count=len(self._mcp_manager.get_tools()))

    # Skill tools (raw names; used only for introspection such as /tool list).
    skill_registry = self._skill_registry_for_build()
    if skill_registry is not None:
      for info in skill_registry.enabled:
        skill = skill_registry.get_skill(info.name)
        if skill is not None:
          tools.extend(skill.tools)

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

    return self.build(
      repo_map=self._repo_map,
      git_summary=self._git_summary,
      plugin_summary=self._plugin_summary,
      execution_plan=self._execution_plan,
    )

  def get_agent(self) -> Agent | None:
    """Return the currently built Agent, if any."""
    return self._agent


class _PrefixedSkillRegistry(SkillRegistry):
  """SkillRegistry that exposes skill tools with namespace prefixes.

  This lets the lazy skill expansion in nonoka-agent register prefixed skill
  tools (``skill__<skill>__<tool>``) instead of raw names, preventing name
  collisions with OpenCode native tools.
  """

  def __init__(self, inner: SkillRegistry):
    self._inner = inner

  def discover(self) -> dict[str, Any]:
    return self._inner.discover()

  @property
  def available(self) -> list[Any]:
    return self._inner.available

  @property
  def enabled(self) -> list[Any]:
    return self._inner.enabled

  def get_skill(self, name: str) -> Any | None:
    return self._inner.get_skill(name)

  def get_tools(self) -> list[Capability]:
    """Return tools from enabled skills with a per-skill namespace prefix."""
    tools: list[Capability] = []
    for info in self.enabled:
      skill = self.get_skill(info.name)
      if skill is None:
        continue
      for tool in skill.tools:
        tools.append(PrefixedCapability(tool, f"skill__{skill.name}__"))
    return tools

  def build_registry_block(self) -> str:
    return self._inner.build_registry_block()
