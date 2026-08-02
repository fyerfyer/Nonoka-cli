"""Orchestrator — coordinates config, agent, runner, MCP, and execution."""

from __future__ import annotations

import hashlib
import os
import subprocess
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
from nonoka.observability import ObservabilityPipeline, SQLiteEventStore
from nonoka.safety import SafetyPolicy
from nonoka.skills.registry import SkillRegistry

from nonoka_cli.builtin_skills import bundled_skills_path, enabled_skill_names
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig, MCPServerConfigModel
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.core.git_service import GitService, build_git_service
from nonoka_cli.core.mcp_service import MCPService
from nonoka_cli.core.plugin_manifest import (
  LoadedPluginManifest,
  PluginManifestLoader,
  format_manifest_summary,
  merge_manifests,
)
from nonoka_cli.core.project_agents import (
  compile_project_agents,
  effective_agent_definitions,
  effective_dynamic_agent_definition,
)
from nonoka_cli.core.repo_map_service import RepoMapService, build_repo_map_service
from nonoka_cli.core.runner_service import RunnerService
from nonoka_cli.core.session_service import SessionService
from nonoka_cli.core.task_state import TaskStateService
from nonoka_cli.core.tool_output_policy import ToolOutputPolicy
from nonoka_cli.core.tool_service import ToolService
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.safety import require_sandbox
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
    event_db_path: Path | str | None = None,
  ):
    self._config = config
    self._config_manager = config_manager
    self._session_service = session_service or SessionService(db_path=db_path)
    self._event_db_path = Path(event_db_path) if event_db_path is not None else None
    self._mcp_manager = mcp_manager or MCPManager()
    self._tool_loader = tool_loader
    self._agent_factory: AgentFactory | None = None
    self._runner_service: RunnerService | None = None
    self._tool_service: ToolService | None = None
    self._mcp_service: MCPService | None = None
    self._git_service: GitService | None = None
    self._repo_map_service: RepoMapService | None = None
    self._plugin_manifests: list[Any] = []
    self._loaded_plugin_manifests: list[LoadedPluginManifest] = []
    self._project_agent_tools: list[Capability] = []
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

    if self._config.safety.required:
      try:
        await require_sandbox(self._config.safety, Path.cwd())
      except RuntimeError as exc:
        raise ConfigError(f"Required sandbox preflight failed: {exc}") from exc

    if self._config.mcp_servers:
      try:
        await self._mcp_manager.start_all(self._config.mcp_servers)
      except MCPRestartExhaustedError as exc:
        logger.error("mcp_startup_partial_failure", error=str(exc))

    # Services that depend only on config/working_dir are created eagerly.
    self._git_service = build_git_service(
      working_dir=Path.cwd(),
      config=self._config,
    )
    self._repo_map_service = build_repo_map_service(
      working_dir=Path.cwd(),
      config=self._config,
    )
    # Load project plugin manifests before building the Agent so that
    # allowed_tools / plugin summary can be taken into account.
    manifest_loader = PluginManifestLoader(extra_paths=list(self._config.plugins.manifests))
    self._loaded_plugin_manifests = manifest_loader.load_with_sources(Path.cwd())
    self._plugin_manifests = [loaded.manifest for loaded in self._loaded_plugin_manifests]
    if self._tool_loader is None:
      self._tool_loader = ToolLoader(self._effective_tool_paths(self._config, Path.cwd()))
    allowed_tools = self._effective_allowed_tools()
    if os.getenv("NONOKA_DISABLE_PROJECT_AGENTS", "").strip().lower() in {"1", "true", "yes"}:
      logger.info("project_agents_disabled_by_environment")
      self._project_agent_tools = []
    else:
      compilation = compile_project_agents(
        effective_agent_definitions(self._loaded_plugin_manifests),
        ToolOutputPolicy.from_config(self._config.tool_output.model_dump()),
        effective_dynamic_agent_definition(self._loaded_plugin_manifests),
      )
      for diagnostic in compilation.diagnostics:
        log = logger.warning if diagnostic.level == "warning" else logger.error
        log(
          "project_agent_configuration_diagnostic",
          level=diagnostic.level,
          role=diagnostic.role,
          source=str(diagnostic.source) if diagnostic.source else None,
          message=diagnostic.message,
        )
      self._project_agent_tools = compilation.tools

    self._agent_factory = AgentFactory(
      self._config,
      mcp_manager=self._mcp_manager,
      tool_loader=self._tool_loader,
      allowed_tools=allowed_tools,
      project_agent_tools=self._project_agent_tools,
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
      observability=ObservabilityPipeline(
        SQLiteEventStore(
          os.getenv(
            "NONOKA_EVENT_DB",
            str(
              self._event_db_path
              or Path.home() / ".local" / "share" / "nonoka" / "events.db"
            ),
          )
        )
      ),
      **self._runner_cache_options(),
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

  def _runner_cache_options(self) -> dict[str, Any]:
    """Keep cache construction at the CLI boundary, not in agent defaults."""
    if not self._config or not self._config.cache.enabled:
      return {}
    from nonoka.core.cache import SQLiteResponseCache

    options: dict[str, Any] = {
      "response_cache": SQLiteResponseCache(self._config.cache.path),
      "cache_ttl_seconds": self._config.cache.ttl_seconds,
    }
    cache = self._config.cache
    if cache.semantic_enabled and cache.embedding_model and cache.embedding_api_base:
      from nonoka_cli.core.semantic_cache import (
        OpenAICompatibleEmbedder,
        SQLiteSemanticResponseCache,
      )

      workspace = Path.cwd()
      scope = self._semantic_cache_scope(workspace)
      if scope is None:
        logger.warning("semantic_cache_disabled_without_revision_scope")
        return options
      options.update(
        {
          "semantic_cache": SQLiteSemanticResponseCache(cache.path),
          "semantic_embedder": OpenAICompatibleEmbedder(
            api_base=cache.embedding_api_base,
            model=cache.embedding_model,
            api_key_env=cache.embedding_api_key_env,
            dimensions=cache.embedding_dimensions,
          ),
          "semantic_threshold": cache.semantic_threshold,
          # The workspace may change during an OpenCode session.  Recompute the
          # revision fingerprint per completion so semantic reuse never crosses
          # a write made earlier in the same task.
          "cache_namespace": lambda: self._semantic_cache_scope(workspace),
        }
      )
    return options

  def _semantic_cache_scope(self, workspace: Path) -> str | None:
    """Return a content-sensitive scope or disable semantic reuse conservatively."""
    try:

      def git(*args: str) -> bytes:
        return subprocess.run(
          ["git", *args],
          cwd=workspace,
          check=True,
          stdout=subprocess.PIPE,
          stderr=subprocess.DEVNULL,
        ).stdout

      head = git("rev-parse", "HEAD")
      diff = git("diff", "--no-ext-diff", "--binary", "HEAD", "--")
      untracked = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
      digest = hashlib.sha256(head + diff)
      for raw_path in untracked:
        if not raw_path:
          continue
        path = workspace / raw_path.decode("utf-8", errors="surrogateescape")
        if path.is_file():
          digest.update(raw_path)
          digest.update(hashlib.sha256(path.read_bytes()).digest())
      index = workspace / self._config.repo_map.index_path
      if index.is_file():
        digest.update(hashlib.sha256(index.read_bytes()).digest())
      digest.update(hashlib.sha256(self._config.system_prompt.encode("utf-8")).digest())
      digest.update(str(workspace.resolve()).encode("utf-8"))
      return f"semantic-v2:{digest.hexdigest()}"
    except (OSError, subprocess.CalledProcessError):
      return None

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

    output_policy = ToolOutputPolicy.from_config(self._config.tool_output.model_dump())

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
    return format_manifest_summary(
      merged,
      agent_tool_names=[tool.name for tool in self._project_agent_tools],
    )

  def _effective_allowed_tools(self) -> list[str]:
    """Return the merged allowed-tools list from all loaded plugin manifests."""
    if not self._plugin_manifests:
      return []
    merged = merge_manifests(self._plugin_manifests)
    return merged.allowed_tools

  def _effective_tool_paths(self, config: CLIConfig, cwd: Path) -> list[Path]:
    """Include conventional project-plugin tools without extra YAML wiring."""
    paths = [Path(path).expanduser() for path in config.tool_paths]
    project_plugin_tools = cwd / ".nonoka" / "tools"
    if self._plugin_manifests and project_plugin_tools not in paths:
      paths.append(project_plugin_tools)
    return paths

  def _on_config_changed(self, config: CLIConfig) -> None:
    """ConfigManager hot-reload listener: update local reference only."""
    self._config = config
    logger.info("config_reference_updated", model=config.model)

  def _build_skill_registry(
    self,
    cwd: Path | None = None,
  ) -> SkillRegistry | None:
    """Build a SkillRegistry from config, optionally scoped to a cwd."""
    if cwd is None and self._agent_factory is not None:
      return self._agent_factory.skill_registry
    # SkillRegistry applies later paths as overrides. Keep the shipped skills
    # first so a project-local skill can tailor the same workflow safely.
    search_paths: list[Path] = [bundled_skills_path()]
    if cwd is not None:
      search_paths.extend(
        [
          cwd / ".nonoka" / "skills",
          cwd / "skills",
        ]
      )
    return SkillRegistry(
      enabled=enabled_skill_names(list(self._config.skills)),
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
      safety_policy=SafetyPolicy(
        allowed_roots=[working_dir, *self._config.safety.allowed_roots],
      )
      if self._config.safety.enabled
      else None,
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
    require_focused_verification: bool = False,
    verification_enforcement: str = "strict",
    max_completion_corrections: int = 1,
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
      require_focused_verification=require_focused_verification,
      verification_enforcement=verification_enforcement,
      max_completion_corrections=max_completion_corrections,
    )
    self._agent_factory.build()

  async def reload_config(self) -> CLIConfig:
    """Hot-reload configuration from disk and rebuild the Agent."""
    self._ensure_initialized()
    if self._config_manager is None:
      raise OrchestratorError("No ConfigManager available. Hot-reload is disabled.")

    old_config = self._config.model_copy(deep=True)
    try:
      new_config = self._config_manager.reload()
    except ConfigError:
      logger.warning("config_reload_failed")
      raise

    self._config = new_config

    try:
      # MCPs and project agents are runtime dependencies, not just prompt
      # text. Recreate them so `/reload` applies an edited mcp_servers block
      # and a newly-written .nonoka/plugin.json to the next turn.
      if old_config.mcp_servers != new_config.mcp_servers:
        await self._mcp_manager.stop_all()
        self._mcp_manager = MCPManager()
        if new_config.mcp_servers:
          await self._mcp_manager.start_all(new_config.mcp_servers)

      manifest_loader = PluginManifestLoader(extra_paths=list(new_config.plugins.manifests))
      self._loaded_plugin_manifests = manifest_loader.load_with_sources(Path.cwd())
      self._plugin_manifests = [loaded.manifest for loaded in self._loaded_plugin_manifests]
      if self._tool_loader is not None:
        self._tool_loader.search_paths = self._effective_tool_paths(new_config, Path.cwd())
        self._tool_loader.reload()
      if os.getenv("NONOKA_DISABLE_PROJECT_AGENTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
      }:
        self._project_agent_tools = []
      else:
        compilation = compile_project_agents(
          effective_agent_definitions(self._loaded_plugin_manifests),
          ToolOutputPolicy.from_config(new_config.tool_output.model_dump()),
          effective_dynamic_agent_definition(self._loaded_plugin_manifests),
        )
        self._project_agent_tools = compilation.tools
        for diagnostic in compilation.diagnostics:
          log = logger.warning if diagnostic.level == "warning" else logger.error
          log(
            "project_agent_configuration_diagnostic",
            level=diagnostic.level,
            role=diagnostic.role,
            source=str(diagnostic.source) if diagnostic.source else None,
            message=diagnostic.message,
          )

      if self._agent_factory is not None:
        self._agent_factory.reconfigure(
          new_config,
          mcp_manager=self._mcp_manager,
          tool_loader=self._tool_loader,
          allowed_tools=self._effective_allowed_tools(),
          project_agent_tools=self._project_agent_tools,
        )
        if self._runner_service is not None:
          active_agent = self._agent_factory.get_agent()
          if active_agent is not None:
            refreshed = await self._runner_service.refresh_persisted_session_limits(
              session_id=self.session_id,
              agent=active_agent,
            )
            logger.info(
              "reloaded_session_runtime_limits",
              session_id=self.session_id,
              refreshed=refreshed,
              max_model_turns=getattr(active_agent, "max_turns", None),
            )
      self._tool_service = ToolService(self._agent_factory, self._tool_loader)
      self._mcp_service = MCPService(self._mcp_manager, self._agent_factory)
    except Exception as exc:
      self._config = old_config
      if self._agent_factory is not None:
        self._agent_factory.reconfigure(
          old_config,
          mcp_manager=self._mcp_manager,
          tool_loader=self._tool_loader,
          allowed_tools=self._effective_allowed_tools(),
          project_agent_tools=self._project_agent_tools,
        )
      raise OrchestratorError(f"Failed to apply reloaded configuration: {exc}") from exc

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
