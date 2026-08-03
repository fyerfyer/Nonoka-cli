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
  CompositeSkillRegistry,
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
  from nonoka.ext.coding import WorkspaceProgressExtension
except ImportError:  # Compatibility with framework builds before extensions.
  WorkspaceProgressExtension = None  # type: ignore[assignment,misc]

try:
  from nonoka import CompletionContract, RuntimeLimits
except ImportError:  # Published nonoka 1.3.5 predates persisted runtime policy.
  CompletionContract = None  # type: ignore[assignment,misc]
  RuntimeLimits = None  # type: ignore[assignment,misc]

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

from nonoka_cli.bridge.nonoka_tools import get_hosted_tools
from nonoka_cli.builtin_skills import bundled_skills_path, enabled_skill_names
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
- Preserve volatile evidence before inspecting it with tools that may mutate it:
  copy databases with their WAL/journal files, logs, crash dumps, archives, and
  generated artifacts to a safe path first.
- Before declaring the task complete, turn every requested file, behavior, and
  output into an acceptance checklist and run the relevant checks. For a
  service, start it and make a real health/request check; for a numerical or
  data transformation task, test its required properties and output files.
- If a command fails for the same environmental reason twice, do not keep
  retrying it. Inspect the error, choose a materially different fallback, or
  report the blocked dependency.
- Bound expensive exploration and validation commands with a timeout. Prefer
  small representative checks before an intentionally slow full baseline.
- Once the requested change has a successful focused check, stop and report the
  result. Do not create optional verification artifacts or rerun equivalent
  checks unless the evidence identifies a concrete unmet requirement.
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
  "- Answer greetings, simple questions, and requests for explanation directly. "
  "Do not explore the workspace or call tools unless the user asks for repository work.\n"
  "- For repository work, stay within the current working directory. Do not inspect "
  "parent directories or unrelated paths unless the user explicitly asks.\n"
  "- Prefer reading files before editing them.\n"
  "- Treat an unambiguous task instruction as authorization to perform its required "
  "changes. Do not stop to ask whether to apply a remediation, implementation, or "
  "other explicitly requested modification.\n"
  "- After a bounded inspection, act on the requested changes and verify them; do "
  "not finish with an audit or plan when implementation remains required.\n"
  "- Run build/test commands when available and report the result.\n"
  "- Preserve volatile evidence (for example databases with WAL/journals, logs, and "
  "crash artifacts) before using a tool that could mutate it.\n"
  "- Before completing a task that changes the workspace, verify every requested "
  "output and behavior; start services and "
  "make a real health/request check when applicable.\n"
  "- Do not repeatedly retry the same environmental failure. Use a distinct fallback "
  "or report the dependency as blocked.\n"
  "- Bound expensive commands and validate on a small representative case before a "
  "known-slow full baseline.\n"
  "- For a workspace-changing task, run the final focused check as "
  "`NONOKA_VERIFY=focused <command>`. After it passes, "
  "complete TODO/progress bookkeeping before that final check because tools are disabled "
  "as soon as the check passes. "
  "stop and report the result. "
  "Do not create optional verification artifacts or rerun equivalent checks without "
  "a concrete unmet requirement.\n"
  "- Keep responses concise but thorough.\n\n"
  "You operate in the user's current working directory.\n"
)

# Older ``nonoka init`` versions persisted this as a project-level prompt.
# Treat it as a built-in default so it does not override newer OpenCode
# guidance forever. A genuinely custom ``system_prompt`` remains authoritative.
_LEGACY_OPENCODE_INIT_SYSTEM_PROMPT = (
  "You are nonoka-cli, an autonomous coding assistant running inside OpenCode.\n"
  "Use the tools available to you proactively to complete tasks.\n"
  "For multi-step tasks, always start by calling the todowrite tool to create a plan.\n"
  "Keep responses concise but thorough."
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
    project_agent_tools: list[Capability] | None = None,
    config_path: Path | str | None = None,
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
    config_path: The authoritative configuration file for this server.
    """
    self._config = config
    self._mcp_manager = mcp_manager
    self._tool_loader = tool_loader
    self._skill_registry = skill_registry
    self._allowed_tools = set(allowed_tools or [])
    self._project_agent_tools = list(project_agent_tools or [])
    self._config_path = Path(config_path).expanduser() if config_path else None
    self._agent: Agent | None = None
    self._repo_map: str | None = None
    self._git_summary: str | None = None
    self._plugin_summary: str | None = None
    self._execution_plan: str | None = None
    self._generation_max_turns: int | None = None
    self._generation_temperature: float | None = None
    self._generation_timeout_seconds: float | None = None
    self._generation_wall_timeout_seconds: float | None = None
    self._generation_tool_budget: int | None = None
    self._generation_max_context_bytes: int | None = None
    self._generation_max_external_result_bytes: int | None = None
    self._require_workspace_mutation = False
    self._require_observed_effect = False
    self._require_focused_verification = False
    self._verification_enforcement = "strict"
    self._max_completion_corrections = 1
    self._generation_options_set = False

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

  def _executor_max_turns(self) -> int | None:
    """Return the per-run override, including an explicit unlimited policy."""
    if self._generation_options_set:
      return self._generation_max_turns
    executor = self._config.agents.executor
    # ``max_turns: 5`` was written by older ``config init`` releases even
    # though it was only intended as a framework compatibility default. It
    # cannot complete ordinary MCP onboarding, so retain it only when the
    # executor otherwise carries a user customization.
    if (
      executor.max_turns == 5
      and not executor.model
      and not executor.system_prompt
      and getattr(executor, "max_steps", None) is None
    ):
      return None
    return executor.max_turns

  def _executor_max_steps(self) -> int | None:
    """Return the cumulative tool-call limit for this interactive session."""
    if self._generation_options_set:
      return self._generation_tool_budget
    return getattr(self._config.agents.executor, "max_steps", None)

  def _register_project_agents(
    self,
    builder: AgentBuilder,
    occupied_names: set[str],
  ) -> AgentBuilder:
    """Register project AgentTools after rejecting namespace collisions."""
    collisions = sorted(
      tool.name for tool in self._project_agent_tools if tool.name in occupied_names
    )
    if collisions:
      raise AgentBuildError("Project agent tool name collision(s): " + ", ".join(collisions))
    for tool in self._project_agent_tools:
      builder = builder.tool(tool)
      occupied_names.add(tool.name)
    return builder

  def set_generation_options(
    self,
    *,
    max_turns: int | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
    wall_timeout_seconds: float | None = None,
    tool_budget: int | None = None,
    max_context_bytes: int | None = None,
    max_external_result_bytes: int | None = None,
    require_workspace_mutation: bool = False,
    require_observed_effect: bool = False,
    require_focused_verification: bool = False,
    verification_enforcement: str = "strict",
    max_completion_corrections: int = 1,
  ) -> None:
    """Apply per-run generation limits used by the OpenCode bridge.

    These settings are deliberately held outside the persisted CLI config so
    benchmark runs can be reproduced without mutating a user's configuration.
    ``max_steps`` is the framework's total tool-call budget.
    """
    self._generation_max_turns = max_turns
    self._generation_temperature = temperature
    self._generation_timeout_seconds = timeout_seconds
    self._generation_wall_timeout_seconds = wall_timeout_seconds
    self._generation_tool_budget = tool_budget
    self._generation_max_context_bytes = max_context_bytes
    self._generation_max_external_result_bytes = max_external_result_bytes
    self._require_workspace_mutation = require_workspace_mutation
    self._require_observed_effect = require_observed_effect
    self._require_focused_verification = require_focused_verification
    self._verification_enforcement = verification_enforcement
    self._max_completion_corrections = max_completion_corrections
    self._generation_options_set = True

  def _apply_generation_options(self, builder: AgentBuilder) -> AgentBuilder:
    """Attach optional bridge generation settings with old-framework fallback."""
    if hasattr(builder, "max_steps"):
      # Avoid the framework's hidden default of 50 for interactive sessions.
      # Benchmarks pass an explicit ``toolBudget`` through generation options.
      builder = builder.max_steps(self._executor_max_steps())
    if self._generation_timeout_seconds is not None and hasattr(builder, "timeout"):
      builder = builder.timeout(self._generation_timeout_seconds)
    return builder

  def _finalize_agent(self, agent: Agent) -> Agent:
    """Attach bridge generation and persisted runtime policy to the Agent."""
    updates: dict[str, Any] = {}
    requires_completion_evidence = bool(
      self._require_workspace_mutation
      or self._require_observed_effect
      or self._require_focused_verification
    )
    runtime_max_turns = self._executor_max_turns()
    if runtime_max_turns is not None and requires_completion_evidence:
      # ``maxTurns`` is the work budget. A successful tool call on the last
      # work turn still needs one model call to report the result. The
      # workspace-progress extension makes that reserved turn tool-free as
      # soon as the completion contract is satisfied.
      runtime_max_turns += 1
    if self._generation_temperature is not None:
      updates["temperature"] = self._generation_temperature
    if RuntimeLimits is not None:
      updates["runtime_limits"] = RuntimeLimits(
        max_model_turns=runtime_max_turns,
        max_tool_calls=self._executor_max_steps(),
        wall_timeout_seconds=self._generation_wall_timeout_seconds,
        model_timeout_seconds=self._generation_timeout_seconds,
        max_context_bytes=self._generation_max_context_bytes,
        max_context_tokens=(
          self._config.context.max_tokens if self._config.context.enabled else None
        ),
        reserve_output_tokens=(
          self._config.context.reserve_output_tokens
          if self._config.context.enabled else None
        ),
        compaction_buffer_tokens=(
          self._config.context.compaction_buffer_tokens
          if self._config.context.enabled else None
        ),
        summary_enabled=(
          self._config.context.summary_enabled and self._config.context.enabled
        ),
        max_external_result_bytes=self._generation_max_external_result_bytes,
        max_total_tokens=self._config.budget.max_total_tokens,
        max_cost_usd=self._config.budget.max_cost_usd,
        fail_on_unknown_cost=self._config.budget.fail_on_unknown_cost,
      )
    if requires_completion_evidence and CompletionContract is not None:
      updates["completion_contract"] = CompletionContract(
        require_workspace_mutation=self._require_workspace_mutation,
        require_observed_effect=self._require_observed_effect,
        require_focused_verification=self._require_focused_verification,
        require_complete_observations=True,
        max_corrections=self._max_completion_corrections,
        # The Harbor verifier is authoritative for a concrete workspace. Give
        # the model one evidence-focused correction, then preserve the state
        # for scoring instead of converting an imperfect self-review into an
        # unscorable host error.
        enforcement=self._verification_enforcement,
      )
      if WorkspaceProgressExtension is not None:
        extensions = list(getattr(agent, "extensions", []))
        if not any(
          getattr(extension, "name", None) == "workspace_progress" for extension in extensions
        ):
          extensions.append(WorkspaceProgressExtension(max_exploration_turns=3))
        updates["extensions"] = extensions
    try:
      return replace(agent, **updates)
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
    occupied_names: set[str] = set()

    # Local / built-in tools via ToolRegistry so runtime reloads are reflected
    # without rebuilding the Agent.
    if self._tool_loader is not None:
      registry = self._tool_loader.load_all()
      builder = builder.tool_registry(registry)
      occupied_names.update(tool.name for tool in registry.get_all())
      logger.debug("agent_factory_local_tools", count=len(registry))

    # Lazy-load skill registry: only names/descriptions are injected eagerly;
    # full guidance is loaded on-demand via the ``load_skill`` tool.
    skill_registry = self._skill_registry_for_build()
    if skill_registry is not None:
      builder = builder.skill_manager(skill_registry).tool(load_skill)
      occupied_names.add(load_skill.name)
      occupied_names.update(tool.name for tool in skill_registry.get_tools())
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
          occupied_names.add(capability.name)
        logger.debug("agent_factory_mcp_tools", count=len(mcp_tools))

    builder = self._register_project_agents(builder, occupied_names)
    self._agent = self._finalize_agent(self._apply_generation_options(builder).build())
    return self._agent

  def build_title_agent(self) -> Agent:
    """Build a one-turn, tool-free agent for OpenCode conversation titles."""
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")
    agent = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(
        "Generate one concise plain-text conversation title. "
        "Do not call tools and do not explain the title."
      )
      .max_turns(1)
      .build()
    )
    if self._generation_temperature is not None:
      try:
        agent = replace(agent, temperature=self._generation_temperature)
      except (AttributeError, TypeError):
        pass
    return agent

  def _build_system_prompt(
    self,
    *,
    repo_map: str | None = None,
    git_summary: str | None = None,
    plugin_summary: str | None = None,
    execution_plan: str | None = None,
  ) -> str:
    """Build the effective system prompt, injecting the current model name."""
    configured_prompt = self._config.system_prompt
    if configured_prompt.strip() == _LEGACY_OPENCODE_INIT_SYSTEM_PROMPT:
      configured_prompt = ""
    base = configured_prompt or _DEFAULT_CODING_SYSTEM_PROMPT
    return SystemPromptBuilder(
      base=base,
      model=self._config.model,
      config_path=self._config_path,
      repo_map=repo_map,
      git_summary=git_summary,
      plugin_summary=plugin_summary,
      execution_plan=execution_plan,
      allowed_tools=list(self._allowed_tools),
      project_agent_tools=[tool.name for tool in self._project_agent_tools],
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

    configured_prompt = self._config.system_prompt
    if configured_prompt.strip() == _LEGACY_OPENCODE_INIT_SYSTEM_PROMPT:
      configured_prompt = ""
    base = configured_prompt or host_system_prompt or _OPENCODE_HOSTED_SYSTEM_PROMPT
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
      external_mcp_registry = ExternalMCPRegistry(
        [
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
        ]
      )
      external_mcp_tool_names = [cap.name for cap in external_mcp_registry.get_tools()]

    # 3. External host-managed skills.
    external_skill_registry: ExternalSkillRegistry | None = None
    external_skill_tool_names: list[str] = []
    if external_skills:
      external_skill_registry = ExternalSkillRegistry(
        [
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
        ]
      )
      external_skill_tool_names = [cap.name for cap in external_skill_registry.get_tools()]

    # 4. Nonoka-managed bridge tools run locally and are deliberately
    # namespaced.  They complement rather than replace the host's coding
    # tools, so a host's implementation detail cannot shadow them.
    hosted_tools = get_hosted_tools()
    hosted_tool_names = [cap.name for cap in hosted_tools]

    # 5. User-defined tools from ``tool_paths`` also execute in the bridge.
    # Do not include nonoka-cli's built-ins here: OpenCode already supplies
    # its native file/shell tools, and registering both would create ambiguous
    # capabilities.  The prefix keeps arbitrary user names from shadowing a
    # host, MCP, skill, or project-agent tool.
    local_tools: list[Capability] = []
    local_tool_names: list[str] = []
    if self._tool_loader is not None:
      local_registry = ToolLoader(
        self._tool_loader.list_tool_paths(),
        include_builtins=False,
      ).load_all()
      local_tools = local_registry.get_all()
      local_tool_names = [f"custom__{cap.name}" for cap in local_tools]

    # 6. Internal skills (configured in nonoka.yaml).
    skill_registry = self._skill_registry_for_build(cwd)

    # 7. Internal MCP tools are prefixed with the server name to avoid collisions.
    mcp_tools: list[tuple[str, Capability]] = []
    mcp_tool_names: list[str] = []
    if self._mcp_manager is not None:
      mcp_tools = self._mcp_manager.get_tools()
      mcp_tool_names = [mcp_tool_name(server, cap.name) for server, cap in mcp_tools]

    # 8. Internal skill tools are prefixed with the skill name.
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
      config_path=self._config_path,
      host_tools=host_tool_names,
      nonoka_tools=hosted_tool_names + local_tool_names,
      external_mcp_tools=external_mcp_tool_names,
      external_skill_tools=external_skill_tool_names,
      internal_mcp_tools=mcp_tool_names,
      internal_skill_tools=skill_tool_names,
      project_agent_tools=[tool.name for tool in self._project_agent_tools],
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
      nonoka_tool_count=len(hosted_tool_names),
      custom_tool_count=len(local_tool_names),
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

    occupied_names = set(
      host_tool_names
      + hosted_tool_names
      + local_tool_names
      + external_mcp_tool_names
      + external_skill_tool_names
      + mcp_tool_names
      + skill_tool_names
    )
    builder = self._register_project_agents(builder, occupied_names)

    # Register bridge-local nonoka tools. They execute in this process, while
    # host_caps below still use the external receipt/resume boundary.
    for cap in hosted_tools:
      builder = builder.tool(cap)

    for cap in local_tools:
      builder = builder.tool(PrefixedCapability(cap, "custom__"))

    # Register one composite manager so host-managed skills do not overwrite
    # configured filesystem skills in AgentBuilder metadata.
    skill_registries: list[SkillRegistry] = []
    if skill_registry is not None:
      skill_registries.append(_PrefixedSkillRegistry(skill_registry))
    if external_skill_registry is not None:
      skill_registries.append(external_skill_registry)
    if skill_registries:
      builder = builder.skill_manager(CompositeSkillRegistry(skill_registries)).tool(load_skill)

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
    # If no cwd is supplied and a registry was injected, reuse it.
    if cwd is None and self._skill_registry is not None:
      return self._skill_registry

    # Search bundled guidance first.  SkillRegistry lets later paths override
    # earlier ones, so a repository can replace a shipped skill with a local
    # policy-specific version when needed.
    search_paths: list[Path] = [bundled_skills_path()]
    if cwd is not None:
      cwd_path = Path(cwd).expanduser().resolve()
      search_paths.extend(
        [
          cwd_path / ".nonoka" / "skills",
          cwd_path / "skills",
        ]
      )
    return SkillRegistry(
      enabled=enabled_skill_names(list(self._config.skills)),
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
    # Keep the conservative contract for unknown host tools, but OpenCode's
    # built-in observation tools have stable semantics and cannot produce a
    # workspace receipt. Marking `read` as mutating made every real OpenCode
    # session fail before its first edit.
    normalized_name = name.lower()
    read_only = normalized_name in {"read", "glob", "grep", "list", "ls", "find"}
    ui_state_only = normalized_name in {"todowrite", "todo"}
    execution = ToolExecution(
      read_only=read_only,
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
      audit_required=not (read_only or ui_state_only),
      **kwargs,
    )
    if not _SUPPORTS_EXECUTION_METADATA:
      capability.execution = execution
      capability.audit_required = not (read_only or ui_state_only)
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

    tools.extend(self._project_agent_tools)

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

  def reconfigure(
    self,
    config: CLIConfig,
    *,
    mcp_manager: MCPManager | None,
    tool_loader: ToolLoader | None,
    allowed_tools: list[str],
    project_agent_tools: list[Capability],
  ) -> Agent:
    """Apply a complete reload while preserving transient run options."""
    self._config = config
    self._mcp_manager = mcp_manager
    self._tool_loader = tool_loader
    self._allowed_tools = set(allowed_tools)
    self._project_agent_tools = list(project_agent_tools)
    return self.rebuild()

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
