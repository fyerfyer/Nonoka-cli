"""Orchestrator — coordinates config, agent, runner, MCP, and execution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
from nonoka import Runner
from nonoka.backends.checkpoint.sqlite import SQLiteCheckpointStore
from nonoka.core.runner import StreamEvent
from nonoka.core.types import Capability
from nonoka.ext.hitl import HumanInTheLoopHooks
from nonoka.ext.hitl.core import HumanApprover, ToolRule

from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig, MCPServerConfigModel
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.core.mcp_service import MCPService
from nonoka_cli.core.runner_service import RunnerService
from nonoka_cli.core.session_service import SessionService
from nonoka_cli.core.tool_service import ToolService
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.skills.manager import SkillManager
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


class Orchestrator:
  """High-level facade: config → agent → runner → sessions → MCP."""

  def __init__(
    self,
    config: CLIConfig | None = None,
    config_manager: ConfigManager | None = None,
    session_service: SessionService | None = None,
    mcp_manager: MCPManager | None = None,
    tool_loader: ToolLoader | None = None,
    skill_manager: SkillManager | None = None,
    db_path: Path | str | None = None,
  ):
    self._config = config
    self._config_manager = config_manager
    self._session_service = session_service or SessionService(db_path=db_path)
    self._mcp_manager = mcp_manager or MCPManager()
    self._tool_loader = tool_loader
    self._skill_manager = skill_manager
    self._agent_factory: AgentFactory | None = None
    self._runner_service: RunnerService | None = None
    self._tool_service: ToolService | None = None
    self._mcp_service: MCPService | None = None
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
    if self._skill_manager is None:
      self._skill_manager = SkillManager()

    self._agent_factory = AgentFactory(
      self._config,
      mcp_manager=self._mcp_manager,
      tool_loader=self._tool_loader,
      skill_manager=self._skill_manager,
    )
    self._agent_factory.build()
    self._tool_service = ToolService(self._agent_factory, self._tool_loader)
    self._mcp_service = MCPService(self._mcp_manager, self._agent_factory)

    db_path = self._session_service.manager.db_path
    runner = Runner(
      checkpoint=SQLiteCheckpointStore(db_path=str(db_path)),
      hooks=self._build_hitl_hooks(),
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

  def _build_hitl_hooks(self) -> HumanInTheLoopHooks | None:
    """Build HITL hooks from CLI config, or None if approvals are disabled."""
    if self._config is None:
      return None

    # Auto-approve disables HITL entirely.
    if getattr(self._config.cli, "auto_approve", False):
      return None

    policy = getattr(self._config.hitl, "policy", "interactive")
    if policy == "auto":
      return None

    dangerous = getattr(self._config.hitl, "dangerous_tools", None) or []
    if not dangerous:
      return None

    rules = [
      ToolRule(tool=name, action="approve", description=f"'{name}' requires approval.")
      for name in dangerous
    ]
    return HumanInTheLoopHooks(
      approver=_DeferredApprover(),
      rules=rules,
      default_action="allow",
      deferred=True,
    )

  def _on_config_changed(self, config: CLIConfig) -> None:
    """ConfigManager hot-reload listener: update local reference only."""
    self._config = config
    logger.info("config_reference_updated", model=config.model)

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

    deps = CLIContext(
      user="local",
      session_id=self.session_id,
      config=self._config,
      working_dir=working_dir or Path.cwd(),
    )

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

    deps = CLIContext(
      user="local",
      session_id=session_id,
      config=self._config,
      working_dir=working_dir or Path.cwd(),
    )

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
  ) -> AsyncIterator[StreamEvent]:
    """Execute *prompt* using only externally-supplied tools.

    The Agent is rebuilt for this turn so that local/MCP tools are excluded.
    Tool execution itself is delegated to the external host (OpenCode).
    """
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    agent = self._agent_factory.build_with_external_tools(
      tools,
      cwd=working_dir or Path.cwd(),
      host_system_prompt=host_system_prompt,
    )

    logger.info(
      "executing_with_external_tools",
      prompt_length=len(prompt),
      session_id=self.session_id,
      tool_count=len(tools),
      working_dir=str(working_dir or Path.cwd()),
    )

    deps = CLIContext(
      user="local",
      session_id=self.session_id,
      config=self._config,
      working_dir=working_dir or Path.cwd(),
    )

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
  ) -> AsyncIterator[StreamEvent]:
    """Resume a session paused for external tool execution.

    The temporary external-tools Agent is rebuilt from the tool definitions
    carried in the current request, because the server process may have been
    recreated between turns.
    """
    self._ensure_initialized()
    if self._agent_factory is None or self._runner_service is None:
      raise OrchestratorError("Orchestrator not fully initialized.")

    agent = self._agent_factory.build_with_external_tools(
      tools,
      cwd=working_dir or Path.cwd(),
      host_system_prompt=host_system_prompt,
    )

    logger.info(
      "resuming_external_tools",
      session_id=session_id,
      tool_results=list(results.keys()),
      tool_count=len(tools),
      working_dir=str(working_dir or Path.cwd()),
    )

    deps = CLIContext(
      user="local",
      session_id=session_id,
      config=self._config,
      working_dir=working_dir or Path.cwd(),
    )

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
    if self._skill_manager is not None:
      self._skill_manager.reload(new_config.skills)

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
