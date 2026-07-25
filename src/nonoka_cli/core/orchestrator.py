"""Orchestrator — coordinates config, agent, runner, MCP, and execution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
from nonoka import Runner
from nonoka.backends.checkpoint.sqlite import SQLiteCheckpointStore
from nonoka.core.context import RunContext
from nonoka.core.hooks import Hooks
from nonoka.core.llm import LLMMessageRole
from nonoka.core.runner import StreamEvent
from nonoka.core.types import Capability
from nonoka.ext.hitl import HumanInTheLoopHooks
from nonoka.ext.hitl.core import HumanApprover, ToolRule
from nonoka.skills.registry import SkillRegistry

from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig, MCPServerConfigModel
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.core.git_service import GitService, build_git_service
from nonoka_cli.core.mcp_service import MCPService
from nonoka_cli.core.planning_service import PlanningService, build_planning_service
from nonoka_cli.core.plugin_manifest import (
  PluginManifestLoader,
  format_manifest_summary,
  merge_manifests,
)
from nonoka_cli.core.repo_map_service import RepoMapService, build_repo_map_service
from nonoka_cli.core.runner_service import RunnerService
from nonoka_cli.core.session_service import SessionService
from nonoka_cli.core.task_state import TaskStateService
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy
from nonoka_cli.core.tool_service import ToolService
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.tools.loader import ToolLoader
from nonoka_cli.utils.errors import ConfigError, MCPRestartExhaustedError, OrchestratorError

logger = structlog.get_logger("nonoka_cli.core")


class _DeferredApprover(HumanApprover):
  """Placeholder approver for deferred HITL mode.

  The CLI never actually blocks on this approver; the bridge layer surfaces
  ``approval_request`` events and the caller resumes with ``resume_approval``.
  If this object is ever awaited, something has gone wrong.
  """

  async def request_approval(self, checkpoint):
    raise RuntimeError("Deferred approver should never be awaited.")

  @property
  def supports_modify(self) -> bool:
    return True


def _paths_from_tool_arguments(arguments: dict[str, Any] | None) -> list[str]:
  """Extract relative file paths from write-tool arguments for cleanup."""
  if not arguments:
    return []
  paths: list[str] = []
  for key in ("path", "file_path", "filePath", "file", "filename"):
    value = arguments.get(key)
    if isinstance(value, str) and value:
      paths.append(value)
  return paths


class Orchestrator:
  """High-level facade: config → agent → runner → sessions → MCP."""

  def __init__(
    self,
    config: CLIConfig | None = None,
    config_manager: ConfigManager | None = None,
    session_service: SessionService | None = None,
    mcp_manager: MCPManager | None = None,
    tool_loader: ToolLoader | None = None,
    db_path: Path | str | None = None,
  ):
    self._config = config
    self._config_manager = config_manager
    self._session_service = session_service or SessionService(db_path=db_path)
    self._mcp_manager = mcp_manager or MCPManager()
    self._tool_loader = tool_loader
    self._agent_factory: AgentFactory | None = None
    self._runner_service: RunnerService | None = None
    self._tool_service: ToolService | None = None
    self._mcp_service: MCPService | None = None
    self._git_service: GitService | None = None
    self._repo_map_service: RepoMapService | None = None
    self._planning_service: PlanningService | None = None
    self._plugin_manifests: list[Any] = []
    self._initialized = False

  @property
  def config(self) -> CLIConfig:
    if self._config is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    return self._config

  @property
  def session_id(self) -> str:
    return self._session_service.current_id

  @property
  def mcp_manager(self) -> MCPManager:
    return self._mcp_manager

  def _ensure_initialized(self) -> None:
    if not self._initialized:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

  async def _touch_session(self) -> None:
    """Best-effort update of the current session's last-active timestamp."""
    try:
      await self._session_service.touch()
    except Exception as touch_exc:
      logger.warning("session_touch_failed", error=str(touch_exc))

  async def initialize(self, config_path: Path | str | None = None) -> None:
    """Load config, start MCP, build agent, create runner, and register session."""
    if self._config is None:
      try:
        if self._config_manager is None:
          self._config_manager = ConfigManager.load(config_path)
        self._config = self._config_manager.get()
      except ConfigError:
        raise
      except Exception as exc:
        raise ConfigError(f"Failed to load configuration: {exc}") from exc

    if self._config.mcp_servers:
      try:
        await self._mcp_manager.start_all(self._config.mcp_servers)
      except MCPRestartExhaustedError as exc:
        logger.error("mcp_startup_partial_failure", error=str(exc))

    if self._tool_loader is None:
      self._tool_loader = ToolLoader(self._config.tool_paths)

    # Services that depend only on config/working_dir are created eagerly.
    self._git_service = build_git_service(
      working_dir=Path.cwd(),
      config=self._config,
    )
    self._repo_map_service = build_repo_map_service(
      working_dir=Path.cwd(),
      config=self._config,
    )
    self._planning_service = build_planning_service(
      working_dir=Path.cwd(),
      config=self._config,
    )

    # Load project plugin manifests before building the Agent so that
    # allowed_tools / plugin summary can be taken into account.
    manifest_loader = PluginManifestLoader(
      extra_paths=list(self._config.plugins.manifests)
    )
    self._plugin_manifests = manifest_loader.load(Path.cwd())
    allowed_tools = self._effective_allowed_tools()

    self._agent_factory = AgentFactory(
      self._config,
      mcp_manager=self._mcp_manager,
      tool_loader=self._tool_loader,
      allowed_tools=allowed_tools,
    )

    # Build optional context blocks for the system prompt.
    repo_map_block = await self._build_repo_map_block()
    git_summary = await self._git_service.status_summary() if self._git_service else None
    plugin_summary = self._build_plugin_summary()

    self._agent_factory.build(
      repo_map=repo_map_block,
      git_summary=git_summary,
      plugin_summary=plugin_summary,
    )
    self._tool_service = ToolService(self._agent_factory, self._tool_loader)
    self._mcp_service = MCPService(self._mcp_manager, self._agent_factory)

    db_path = self._session_service.manager.db_path
    runner = Runner(
      checkpoint=SQLiteCheckpointStore(db_path=str(db_path)),
      hooks=self._build_hooks(),
    )
    self._runner_service = RunnerService(runner)

    await self._session_service.initialize(model=self._config.model)

    if self._config_manager is not None:
      self._config_manager.on_change(self._on_config_changed)

    local_tool_count = len(self._tool_loader.get_loaded()) if self._tool_loader else 0
    logger.info(
      "orchestrator_initialized",
      model=self._config.model,
      session_id=self.session_id,
      db_path=str(db_path),
      mcp_servers=list(self._config.mcp_servers.keys()),
      mcp_tool_count=len(self._mcp_manager.get_tools()),
      local_tool_count=local_tool_count,
      skills=self._config.skills,
    )
    self._initialized = True

  def _build_hooks(self) -> HumanInTheLoopHooks | Hooks | None:
    """Build combined hooks: HITL + context trimming + tool-output pruning."""
    if self._config is None:
      return None

    # Auto-approve disables HITL entirely.
    hitl = None
    if not getattr(self._config.cli, "auto_approve", False):
      policy = getattr(self._config.hitl, "policy", "interactive")
      if policy != "auto":
        dangerous = getattr(self._config.hitl, "dangerous_tools", None) or []
        if dangerous:
          rules = [
            ToolRule(tool=name, action="approve", description=f"'{name}' requires approval.")
            for name in dangerous
          ]
          hitl = HumanInTheLoopHooks(
            approver=_DeferredApprover(),
            rules=rules,
            default_action="allow",
            deferred=True,
          )

    output_policy = ToolOutputPolicy.from_config(
      self._config.tool_output.model_dump()
    )

    async def on_llm_request(ctx, messages, tools):
      if self._config is None:
        return
      # Agent checkpoint memory owns semantic compaction.  Re-trimming that
      # history here can split evidence from the persisted runtime state.  The
      # CLI hook is intentionally limited to host/tool-output normalization.
      if output_policy.enabled:
        for msg in messages:
          if msg.role == LLMMessageRole.TOOL and msg.content:
            pruned = output_policy.apply(
              msg.name or "",
              msg.content,
              msg.tool_call_id,
            )
            if pruned != msg.content:
              msg.content = pruned

    async def on_tool_start(hook_ctx, tool_name, arguments):
      deps = getattr(hook_ctx.session, "deps", None)
      git_service = getattr(deps, "git_service", None) if deps else None
      if git_service is None:
        return
      try:
        run_ctx = RunContext(hook_ctx.session)
        checkpoint = await git_service.checkpoint_before(run_ctx, tool_name, arguments)
        if checkpoint:
          hook_ctx.extra.setdefault("git_checkpoint_before", {})[tool_name] = checkpoint
      except Exception as exc:
        logger.warning("git_checkpoint_hook_failed", error=str(exc), tool=tool_name)

    async def on_tool_end(hook_ctx, tool_name, arguments, result, error):
      deps = getattr(hook_ctx.session, "deps", None)
      git_service = getattr(deps, "git_service", None) if deps else None
      if git_service is None:
        return

      run_ctx = RunContext(hook_ctx.session)
      before_hash = hook_ctx.extra.get("git_checkpoint_before", {}).get(tool_name)

      if error is not None:
        try:
          paths = _paths_from_tool_arguments(arguments)
          await git_service.rollback_last(
            run_ctx,
            to_hash=before_hash,
            paths=paths,
          )
        except Exception as exc:
          logger.warning("git_rollback_hook_failed", error=str(exc), tool=tool_name)
        return

      if git_service.should_checkpoint_after(tool_name):
        try:
          await git_service.checkpoint_after(run_ctx, tool_name, arguments)
        except Exception as exc:
          logger.warning("git_checkpoint_after_hook_failed", error=str(exc), tool=tool_name)

    hooks_kwargs: dict[str, Any] = {
      "on_llm_request": on_llm_request,
      "on_tool_start": on_tool_start,
      "on_tool_end": on_tool_end,
    }

    if hitl is not None:
      return HumanInTheLoopHooks(
        approver=_DeferredApprover(),
        rules=hitl.rules,
        default_action="allow",
        deferred=True,
        **hooks_kwargs,
      )
    return Hooks(**hooks_kwargs)

  async def _build_repo_map_block(self) -> str | None:
    """Build the repo-map system-prompt block, if enabled."""
    if self._repo_map_service is None:
      return None
    return await self._repo_map_service.build_system_prompt_block()

  async def _build_repo_map_block_for_dir(self, cwd: Path) -> str | None:
    """Build the repo-map block for an arbitrary working directory."""
    service = build_repo_map_service(working_dir=cwd, config=self._config)
    return await service.build_system_prompt_block()

  async def _git_status_summary_for_dir(self, cwd: Path) -> str | None:
    """Build the git status summary for an arbitrary working directory."""
    service = build_git_service(working_dir=cwd, config=self._config)
    return await service.status_summary()

  def _build_plugin_summary(self) -> str | None:
    """Build the plugin-manifest system-prompt summary, if any manifests loaded."""
    if not self._plugin_manifests:
      return None
    merged = merge_manifests(self._plugin_manifests)
    return format_manifest_summary(merged)

  def _effective_allowed_tools(self) -> list[str]:
    """Return the merged allowed-tools list from all loaded plugin manifests."""
    if not self._plugin_manifests:
      return []
    merged = merge_manifests(self._plugin_manifests)
    return merged.allowed_tools

  def _on_config_changed(self, config: CLIConfig) -> None:
    """ConfigManager hot-reload listener: update local reference only."""
    self._config = config
    logger.info("config_reference_updated", model=config.model)

  def _build_skill_registry(
    self,
    cwd: Path | None = None,
  ) -> SkillRegistry | None:
    """Build a SkillRegistry from config, optionally scoped to a cwd."""
    if not self._config.skills:
      return None
    if cwd is None and self._agent_factory is not None:
      return self._agent_factory.skill_registry
    search_paths: list[Path] = []
    if cwd is not None:
      search_paths.extend([
          cwd / ".nonoka" / "skills",
          cwd / "skills",
      ])
    return SkillRegistry(
      enabled=list(self._config.skills),
      search_paths=search_paths,
    )

  def _build_cli_context(
    self,
    session_id: str,
    working_dir: Path,
  ) -> CLIContext:
    """Construct a consistent CLIContext for all execution paths."""
    return CLIContext(
      user="local",
      session_id=session_id,
      config=self._config,
      working_dir=working_dir,
      task_state_service=TaskStateService(
        tasks_dir=self._config.task_state.tasks_dir,
        enabled=self._config.task_state.enabled,
        base_dir=working_dir,
      ),
      skill_manager=self._build_skill_registry(working_dir),
      mcp_manager=self._mcp_manager,
      git_service=build_git_service(
        working_dir=working_dir,
        config=self._config,
      ),
      repo_map_service=build_repo_map_service(
        working_dir=working_dir,
        config=self._config,
      ),
      plugin_manifests=self._plugin_manifests,
    )

  async def execute(
    self,
    prompt: str,
    working_dir: Path | None = None,
  ) -> AsyncIterator[StreamEvent]:
    """Execute *prompt* and yield streaming events."""
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    agent = self._agent_factory.get_agent()
    if agent is None:
      raise OrchestratorError("No Agent available. Build failed during initialization.")

    # Optional two-stage Planner/Executor workflow: when a planner model is
    # configured, generate a plan first and rebuild the executor Agent with the
    # plan injected into its system prompt.
    logger.info(
      "execute_planning_check",
      planning_service_exists=self._planning_service is not None,
      planning_enabled=self._planning_service.enabled if self._planning_service else False,
      planner_model=self._planning_service.planner_model if self._planning_service else None,
    )
    if self._planning_service is not None and self._planning_service.enabled:
      try:
        plan = await self._planning_service.plan(prompt)
        logger.info("execute_plan_result", plan_preview=plan[:200] if plan else None)
        if plan and not plan.startswith(("Error", "Planning is disabled")):
          logger.info("execution_plan_generated", plan_length=len(plan))
          agent = self._agent_factory.build(execution_plan=plan)
      except Exception as exc:
        logger.warning("execution_plan_generation_failed", error=str(exc))

    logger.info(
      "executing_prompt",
      prompt_length=len(prompt),
      session_id=self.session_id,
      working_dir=str(working_dir or Path.cwd()),
    )

    cwd = working_dir or Path.cwd()
    deps = self._build_cli_context(self.session_id, cwd)

    try:
      async for event in self._runner_service.run(
        agent, prompt, deps=deps, session_id=self.session_id
      ):
        yield event
    finally:
      await self._touch_session()

  async def resume_approval(
    self,
    session_id: str,
    approvals: dict[str, dict[str, Any]],
    working_dir: Path | None = None,
  ) -> AsyncIterator[StreamEvent]:
    """Resume a session paused for tool-call approvals."""
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    agent = self._agent_factory.get_agent()
    if agent is None:
      raise OrchestratorError("No Agent available. Build failed during initialization.")

    logger.info(
      "resuming_approval",
      session_id=session_id,
      approvals=list(approvals.keys()),
      working_dir=str(working_dir or Path.cwd()),
    )

    cwd = working_dir or Path.cwd()
    deps = self._build_cli_context(session_id, cwd)

    try:
      async for event in self._runner_service.resume_approval(
        agent, deps=deps, session_id=session_id, approvals=approvals
      ):
        yield event
    finally:
      await self._touch_session()

  async def execute_with_external_tools(
    self,
    prompt: str,
    tools: list[Capability],
    working_dir: Path | None = None,
    host_system_prompt: str | None = None,
    external_mcp_servers: list[Any] | None = None,
    external_skills: list[Any] | None = None,
  ) -> AsyncIterator[StreamEvent]:
    """Execute *prompt* using externally-supplied and configured tools.

    The Agent is rebuilt for this turn. Tool execution for host-native and
    host-managed MCP/skill tools is delegated to the external host; internal
    MCP/skill tools are executed locally by nonoka-cli.
    """
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    cwd = working_dir or Path.cwd()
    repo_map_block = await self._build_repo_map_block_for_dir(cwd)
    git_summary = await self._git_status_summary_for_dir(cwd)
    plugin_summary = self._build_plugin_summary()

    # Optional two-stage planning for host-managed (OpenCode) mode.
    execution_plan: str | None = None
    logger.info(
      "external_tools_planning_check",
      planning_service_exists=self._planning_service is not None,
      planning_enabled=self._planning_service.enabled if self._planning_service else False,
      planner_model=self._planning_service.planner_model if self._planning_service else None,
    )
    if self._planning_service is not None and self._planning_service.enabled:
      try:
        plan = await self._planning_service.plan(prompt)
        logger.info("external_tools_plan_result", plan_preview=plan[:200] if plan else None)
        if plan and not plan.startswith(("Error", "Planning is disabled")):
          logger.info("external_execution_plan_generated", plan_length=len(plan))
          execution_plan = plan
      except Exception as exc:
        logger.warning("external_execution_plan_generation_failed", error=str(exc))

    agent = self._agent_factory.build_with_external_tools(
      tools,
      cwd=cwd,
      host_system_prompt=host_system_prompt,
      external_mcp_servers=external_mcp_servers,
      external_skills=external_skills,
      repo_map=repo_map_block,
      git_summary=git_summary,
      plugin_summary=plugin_summary,
      execution_plan=execution_plan,
    )

    logger.info(
      "executing_with_external_tools",
      prompt_length=len(prompt),
      session_id=self.session_id,
      tool_count=len(tools),
      working_dir=str(cwd),
    )

    cwd = working_dir or Path.cwd()
    deps = self._build_cli_context(self.session_id, cwd)

    try:
      async for event in self._runner_service.run(
        agent, prompt, deps=deps, session_id=self.session_id
      ):
        yield event
    finally:
      await self._touch_session()

  async def execute_title(
    self,
    prompt: str,
    working_dir: Path | None = None,
  ) -> AsyncIterator[StreamEvent]:
    """Generate an OpenCode title without exposing local or host tools."""
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")
    agent = self._agent_factory.build_title_agent()
    cwd = working_dir or Path.cwd()
    deps = self._build_cli_context(self.session_id, cwd)
    try:
      async for event in self._runner_service.run(
        agent, prompt, deps=deps, session_id=self.session_id
      ):
        yield event
    finally:
      await self._touch_session()

  async def resume_external_tools(
    self,
    session_id: str,
    results: dict[str, Any],
    tools: list[Capability],
    working_dir: Path | None = None,
    host_system_prompt: str | None = None,
    external_mcp_servers: list[Any] | None = None,
    external_skills: list[Any] | None = None,
  ) -> AsyncIterator[StreamEvent]:
    """Resume a session paused for external tool execution.

    The temporary external-tools Agent is rebuilt from the tool definitions
    carried in the current request, because the server process may have been
    recreated between turns.
    """
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    cwd = working_dir or Path.cwd()
    repo_map_block = await self._build_repo_map_block_for_dir(cwd)
    git_summary = await self._git_status_summary_for_dir(cwd)
    plugin_summary = self._build_plugin_summary()

    # Rebuild the external-tools agent. Planning is not re-run on resume because
    # the plan was already generated before the initial turn.
    agent = self._agent_factory.build_with_external_tools(
      tools,
      cwd=cwd,
      host_system_prompt=host_system_prompt,
      external_mcp_servers=external_mcp_servers,
      external_skills=external_skills,
      repo_map=repo_map_block,
      git_summary=git_summary,
      plugin_summary=plugin_summary,
    )

    logger.info(
      "resuming_external_tools",
      session_id=session_id,
      tool_results=list(results.keys()),
      tool_count=len(tools),
      working_dir=str(cwd),
    )

    cwd = working_dir or Path.cwd()
    deps = self._build_cli_context(session_id, cwd)

    try:
      async for event in self._runner_service.resume_external_tools(
        agent, deps=deps, session_id=session_id, results=results
      ):
        yield event
    finally:
      await self._touch_session()

  async def new_session(self, name: str | None = None) -> str:
    """Create a new session and return its id."""
    return await self._session_service.new(
      model=self.config.model,
      name=name,
    )

  async def switch_session(self, session_id: str) -> SessionInfo:
    """Switch to an existing session."""
    self._ensure_initialized()
    return await self._session_service.switch(session_id)

  async def rename_session(self, name: str) -> SessionInfo:
    """Rename the current session."""
    self._ensure_initialized()
    return await self._session_service.rename(name)

  async def delete_session(self, session_id: str) -> None:
    """Delete a session and its persisted data."""
    self._ensure_initialized()
    await self._session_service.delete(session_id)

  async def list_sessions(self) -> list[SessionInfo]:
    """Return all sessions ordered by last activity descending."""
    self._ensure_initialized()
    return await self._session_service.list()

  async def switch_model(self, model: str) -> None:
    """Switch the active model and rebuild the Agent."""
    self._ensure_initialized()
    if self._agent_factory is None:
      raise OrchestratorError("Agent factory not available.")
    if not model or not model.strip():
      raise ConfigError("Model name cannot be empty.")

    old_model = self._config.model
    self._config.model = model.strip()
    try:
      self._agent_factory.rebuild({"model": self._config.model})
    except Exception as exc:
      self._config.model = old_model
      raise OrchestratorError(f"Failed to switch model to '{model}': {exc}") from exc

    logger.info(
      "model_switched",
      old_model=old_model,
      new_model=self._config.model,
      session_id=self.session_id,
    )

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
  ) -> None:
    """Apply non-persistent per-run options and rebuild the active Agent."""
    self._ensure_initialized()
    if self._agent_factory is None:
      raise OrchestratorError("Agent factory not available.")
    self._agent_factory.set_generation_options(
      max_turns=max_turns,
      temperature=temperature,
      timeout_seconds=timeout_seconds,
      wall_timeout_seconds=wall_timeout_seconds,
      tool_budget=tool_budget,
      max_context_bytes=max_context_bytes,
      max_external_result_bytes=max_external_result_bytes,
      require_workspace_mutation=require_workspace_mutation,
      require_observed_effect=require_observed_effect,
    )
    self._agent_factory.build()

  async def reload_config(self) -> CLIConfig:
    """Hot-reload configuration from disk and rebuild the Agent."""
    self._ensure_initialized()
    if self._config_manager is None:
      raise OrchestratorError("No ConfigManager available. Hot-reload is disabled.")

    old_config = self._config.model_dump()
    try:
      new_config = self._config_manager.reload()
    except ConfigError:
      logger.warning("config_reload_failed")
      raise

    self._config = new_config

    if self._tool_loader is not None:
      self._tool_loader.search_paths = [
        Path(p).expanduser() for p in new_config.tool_paths
      ]
      self._tool_loader.reload()
    if self._agent_factory is not None:
      try:
        self._agent_factory.rebuild(new_config.model_dump())
      except Exception as exc:
        self._config = CLIConfig.model_validate(old_config)
        if self._agent_factory is not None:
          self._agent_factory.rebuild(old_config)
        raise OrchestratorError(f"Failed to rebuild Agent after config reload: {exc}") from exc

    logger.info(
      "config_reloaded_and_applied",
      model=new_config.model,
      session_id=self.session_id,
    )
    return new_config

  def _tools(self) -> ToolService:
    if self._tool_service is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    return self._tool_service

  def list_tools(self) -> list[Any]:
    """Return all tools available to the current Agent."""
    return self._tools().list_tools()

  def get_tool_info(self, name: str) -> dict[str, Any] | None:
    """Return the JSON schema for a named tool, or None if not found."""
    return self._tools().get_tool_info(name)

  def reload_tools(self) -> list[Any]:
    """Reload local tools and rebuild the Agent."""
    self._ensure_initialized()
    return self._tools().reload_tools()

  def _mcps(self) -> MCPService:
    if self._mcp_service is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    return self._mcp_service

  def list_mcp_status(self) -> dict[str, MCPStatus]:
    """Return the status of all configured MCP servers."""
    return self._mcps().list_status()

  async def restart_mcp(self, name: str) -> MCPStatus:
    """Restart a configured MCP server."""
    return await self._mcps().restart(name)

  async def add_mcp_server(
    self,
    name: str,
    config: MCPServerConfigModel,
  ) -> MCPStatus:
    """Add, persist, and start a new MCP server at runtime."""
    self._ensure_initialized()
    return await self._mcps().add_server(name, config, self._config)

  async def shutdown(self) -> None:
    """Graceful shutdown — stop MCP servers and clean up resources."""
    if self._config_manager is not None:
      self._config_manager.remove_listener(self._on_config_changed)
    await self._session_service.close()
    await self._mcp_manager.stop_all()
    logger.info("orchestrator_shutdown")
    self._initialized = False
