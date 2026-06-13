"""Orchestrator — coordinates config, agent, runner, MCP, and execution."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import structlog
from nonoka import Runner
from nonoka.backends.checkpoint.sqlite import SQLiteCheckpointStore
from nonoka.core.runner import StreamEvent

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig, MCPServerConfigModel
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.sessions.manager import SessionManager
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.skills.manager import SkillManager
from nonoka_cli.tools.loader import ToolLoader
from nonoka_cli.utils.errors import (
  ConfigError,
  MCPRestartExhaustedError,
  OrchestratorError,
  SessionError,
  SessionNotFoundError,
)

logger = structlog.get_logger("nonoka_cli.core")


class Orchestrator:
  """Orchestration layer — the bridge between Shell and nonoka kernel.

  Responsibilities:
  - Load configuration
  - Build Agent and Runner
  - Start and manage MCP servers
  - Execute prompts via nonoka's streaming ReAct API
  - Manage sessions (create, switch, list, rename, delete)
  - Persist session metadata and reuse nonoka checkpoint store
  - Support model switching and config hot-reload while preserving context
  """

  def __init__(
    self,
    config: CLIConfig | None = None,
    config_manager: ConfigManager | None = None,
    session_manager: SessionManager | None = None,
    mcp_manager: MCPManager | None = None,
    tool_loader: ToolLoader | None = None,
    skill_manager: SkillManager | None = None,
    db_path: Path | str | None = None,
  ):
    """Initialize the orchestrator.

    Args:
      config: Pre-loaded configuration. If None, will be loaded on initialize().
      config_manager: Optional ConfigManager for hot-reload support.
      session_manager: Optional SessionManager for persistence. If None, one
        will be created using db_path during initialize().
      mcp_manager: Optional MCPManager. If None, one is created during
        initialize().
      tool_loader: Optional ToolLoader for local / built-in tools.
      skill_manager: Optional SkillManager for loading configured skills.
      db_path: Path to the SQLite database used for sessions and nonoka
        checkpoints. Defaults to ~/.local/share/nonoka/nonoka.db.
    """
    self._config = config
    self._config_manager = config_manager
    self._session_manager = session_manager
    self._mcp_manager = mcp_manager or MCPManager()
    self._tool_loader = tool_loader
    self._skill_manager = skill_manager
    self._db_path = db_path
    self._agent_factory: AgentFactory | None = None
    self._runner: Runner | None = None
    self._session_id: str = str(uuid.uuid4())
    self._initialized = False

  @property
  def config(self) -> CLIConfig:
    """Current configuration."""
    if self._config is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    return self._config

  @property
  def config_manager(self) -> ConfigManager | None:
    """Current configuration manager, if any."""
    return self._config_manager

  @property
  def session_id(self) -> str:
    """Current session identifier."""
    return self._session_id

  @property
  def mcp_manager(self) -> MCPManager:
    """Current MCP manager."""
    return self._mcp_manager

  async def get_current_session(self) -> SessionInfo:
    """Return metadata for the current active session.

    Raises:
      OrchestratorError: If not initialized or the session is not found.
    """
    if not self._initialized or self._session_manager is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    info = await self._session_manager.get(self._session_id)
    if info is None:
      raise OrchestratorError(f"Current session not found: {self._session_id}")
    return info

  @property
  def session_manager(self) -> SessionManager | None:
    """Current session manager, if initialized."""
    return self._session_manager

  @property
  def agent_factory(self) -> AgentFactory | None:
    """Current AgentFactory, if initialized."""
    return self._agent_factory

  async def initialize(self, config_path: Path | str | None = None) -> None:
    """Load config, start MCP servers, build agent, and create runner.

    Args:
      config_path: Optional explicit path to config file.

    Raises:
      ConfigError: If configuration cannot be loaded.
      OrchestratorError: If agent or runner cannot be built.
    """
    if self._config is None:
      try:
        if self._config_manager is None:
          self._config_manager = ConfigManager.load(config_path)
        self._config = self._config_manager.get()
      except ConfigError:
        raise
      except Exception as exc:
        raise ConfigError(f"Failed to load configuration: {exc}") from exc

    # Start MCP servers before building the Agent so tools are available.
    if self._config.mcp_servers:
      try:
        await self._mcp_manager.start_all(self._config.mcp_servers)
      except MCPRestartExhaustedError as exc:
        # Log and continue; failed servers are tracked as unhealthy.
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

    # Use a persistent checkpoint store shared with the session index.
    if self._session_manager is None:
      self._session_manager = SessionManager(db_path=self._db_path)

    db_path = self._session_manager.db_path
    self._runner = Runner(checkpoint=SQLiteCheckpointStore(db_path=str(db_path)))

    # Ensure the current session exists in the index.
    existing = await self._session_manager.get(self._session_id)
    if existing is None:
      await self._session_manager.create(
        session_id=self._session_id,
        model=self._config.model,
      )

    # Subscribe to config changes so system_prompt etc. take effect
    # on the next Agent rebuild (model changes are handled explicitly).
    if self._config_manager is not None:
      self._config_manager.on_change(self._on_config_changed)

    local_tool_count = len(self._tool_loader.get_loaded()) if self._tool_loader else 0
    logger.info(
      "orchestrator_initialized",
      model=self._config.model,
      session_id=self._session_id,
      db_path=str(db_path),
      mcp_servers=list(self._config.mcp_servers.keys()),
      mcp_tool_count=len(self._mcp_manager.get_tools()),
      local_tool_count=local_tool_count,
      skills=self._config.skills,
    )
    self._initialized = True

  def _on_config_changed(self, config: CLIConfig) -> None:
    """Internal listener for ConfigManager hot-reload events.

    Only updates the local reference. Orchestrator.reload_config() performs
    the explicit Agent rebuild to avoid duplicate work.
    """
    self._config = config
    logger.info(
      "config_reference_updated",
      model=config.model,
      system_prompt_length=len(config.system_prompt),
    )

  async def execute(self, prompt: str) -> AsyncIterator[StreamEvent]:
    """Execute a user prompt and yield streaming events.

    Args:
      prompt: The user's input text.

    Yields:
      StreamEvent objects from nonoka's ReAct streaming API.

    Raises:
      OrchestratorError: If not initialized or execution fails.
    """
    if not self._initialized or self._agent_factory is None or self._runner is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    agent = self._agent_factory.get_agent()
    if agent is None:
      raise OrchestratorError("No Agent available. Build failed during initialization.")

    logger.info("executing_prompt", prompt_length=len(prompt), session_id=self._session_id)

    deps = CLIContext(
      user="local",
      session_id=self._session_id,
      config=self._config,
      working_dir=Path.cwd(),
    )

    try:
      async for event in self._runner.run_react_stream(
        agent, prompt, deps=deps, session_id=self._session_id
      ):
        yield event
    except Exception as exc:
      logger.error("execution_failed", error=str(exc))
      raise OrchestratorError(f"Execution failed: {exc}") from exc
    finally:
      # Update last_active and message count even if the stream errors.
      if self._session_manager is not None:
        try:
          await self._session_manager.touch(self._session_id)
        except Exception as touch_exc:
          logger.warning("session_touch_failed", error=str(touch_exc))

  async def new_session(self, name: str | None = None) -> str:
    """Create a new session, discarding the old one.

    Args:
      name: Optional human-readable name for the session.

    Returns:
      The new session_id.
    """
    self._session_id = str(uuid.uuid4())

    if self._initialized and self._session_manager is not None:
      await self._session_manager.create(
        session_id=self._session_id,
        model=self._config.model,
        name=name,
      )

    logger.info("new_session_created", session_id=self._session_id, name=name)
    return self._session_id

  async def switch_session(self, session_id: str) -> SessionInfo:
    """Switch to an existing session and resume its context.

    Args:
      session_id: Session identifier to switch to.

    Returns:
      The session metadata.

    Raises:
      SessionNotFoundError: If the session does not exist.
      OrchestratorError: If not initialized.
    """
    if not self._initialized or self._session_manager is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    info = await self._session_manager.get(session_id)
    if info is None:
      raise SessionNotFoundError(f"Session not found: {session_id}")

    self._session_id = session_id
    logger.info("session_switched", session_id=session_id)
    return info

  async def rename_session(self, name: str) -> SessionInfo:
    """Rename the current session.

    Args:
      name: New human-readable name.

    Returns:
      Updated session metadata.
    """
    if not self._initialized or self._session_manager is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    return await self._session_manager.rename(self._session_id, name)

  async def delete_session(self, session_id: str) -> None:
    """Delete a session and its persisted data.

    Args:
      session_id: Session identifier to delete.

    Raises:
      SessionError: If trying to delete the active session.
      SessionNotFoundError: If the session does not exist.
      OrchestratorError: If not initialized.
    """
    if not self._initialized or self._session_manager is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    if session_id == self._session_id:
      raise SessionError(
        "Cannot delete the active session. Switch to another session first."
      )

    await self._session_manager.delete(session_id)

  async def list_sessions(self) -> list[SessionInfo]:
    """Return all sessions ordered by last activity descending."""
    if not self._initialized or self._session_manager is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    return await self._session_manager.list()

  async def switch_model(self, model: str) -> None:
    """Switch the active model, rebuild Agent, and keep the session context.

    Args:
      model: New model identifier (e.g. "gpt-4o").

    Raises:
      OrchestratorError: If not initialized.
      ConfigError: If the model value is invalid.
    """
    if not self._initialized or self._agent_factory is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    if not model or not model.strip():
      raise ConfigError("Model name cannot be empty.")

    old_model = self._config.model
    self._config.model = model.strip()

    try:
      self._agent_factory.rebuild({"model": self._config.model})
    except Exception as exc:
      # Roll back on failure
      self._config.model = old_model
      raise OrchestratorError(f"Failed to switch model to '{model}': {exc}") from exc

    logger.info(
      "model_switched",
      old_model=old_model,
      new_model=self._config.model,
      session_id=self._session_id,
    )

  async def reload_config(self) -> CLIConfig:
    """Hot-reload configuration from disk and rebuild the Agent.

    Returns:
      The newly loaded configuration.

    Raises:
      ConfigError: If configuration cannot be loaded or validated.
      OrchestratorError: If not initialized.
    """
    if not self._initialized:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    if self._config_manager is None:
      raise OrchestratorError("No ConfigManager available. Hot-reload is disabled.")

    old_config = self._config.model_dump()
    try:
      new_config = self._config_manager.reload()
    except ConfigError:
      # Do not rebuild; keep current Agent running
      logger.warning("config_reload_failed")
      raise

    self._config = new_config

    # Refresh local tools and skills when paths / names change.
    if self._tool_loader is not None:
      self._tool_loader.search_paths = [
        Path(p).expanduser() for p in new_config.tool_paths
      ]
      self._tool_loader.reload()
    if self._skill_manager is not None:
      self._skill_manager.reload(new_config.skills)

    # Rebuild Agent with new configuration
    if self._agent_factory is not None:
      try:
        self._agent_factory.rebuild(new_config.model_dump())
      except Exception as exc:
        # Roll back to previous config
        self._config = CLIConfig.model_validate(old_config)
        if self._agent_factory is not None:
          self._agent_factory.rebuild(old_config)
        raise OrchestratorError(f"Failed to rebuild Agent after config reload: {exc}") from exc

    logger.info(
      "config_reloaded_and_applied",
      model=new_config.model,
      session_id=self._session_id,
    )
    return new_config

  # ------------------------------------------------------------------ #
  # Tool operations
  # ------------------------------------------------------------------ #

  def list_tools(self) -> list[Any]:
    """Return all tools available to the current Agent.

    Includes built-ins, local tools, and MCP tools.
    """
    if self._agent_factory is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    return self._agent_factory.list_all_tools()

  def get_tool_info(self, name: str) -> dict[str, Any] | None:
    """Return the JSON schema for a named tool, or None if not found."""
    if self._agent_factory is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    tool = self._agent_factory.get_tool(name)
    if tool is None:
      return None
    return tool.to_json_schema()

  def reload_tools(self) -> list[Any]:
    """Reload local tools and rebuild the Agent.

    Returns:
      The updated tool list.
    """
    if not self._initialized or self._agent_factory is None:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")
    if self._tool_loader is not None:
      self._tool_loader.reload()
    self._agent_factory.rebuild()
    return self.list_tools()

  # ------------------------------------------------------------------ #
  # MCP operations
  # ------------------------------------------------------------------ #

  def list_mcp_status(self) -> dict[str, MCPStatus]:
    """Return the status of all configured MCP servers."""
    return self._mcp_manager.list_status()

  async def restart_mcp(self, name: str) -> MCPStatus:
    """Restart a configured MCP server.

    Args:
      name: Server name as declared in configuration.

    Returns:
      The updated server status.

    Raises:
      MCPConnectionError: If the server is not configured.
      MCPRestartExhaustedError: If restart attempts are exhausted.
    """
    await self._mcp_manager.restart(name)
    return self._mcp_manager.get_status(name)

  async def add_mcp_server(
    self,
    name: str,
    config: MCPServerConfigModel,
  ) -> MCPStatus:
    """Add and start a new MCP server at runtime.

    The server configuration is persisted to ``~/.config/nonoka/mcp_servers.yaml``
    so it survives CLI restarts. The Agent is rebuilt automatically so the new
    tools are immediately available.

    Args:
      name: Server name.
      config: Server configuration.

    Returns:
      The status of the newly started server.

    Raises:
      OrchestratorError: If not initialized.
      MCPRestartExhaustedError: If the server fails to start.
      ConfigError: If the configuration cannot be persisted.
    """
    if not self._initialized:
      raise OrchestratorError("Orchestrator not initialized. Call initialize() first.")

    # Persist to side-car file.
    mcp_servers = ConfigLoader.load_mcp_servers()
    if name in mcp_servers:
      raise ConfigError(f"MCP server '{name}' already exists.")
    mcp_servers[name] = config.model_dump()
    ConfigLoader.save_mcp_servers(mcp_servers)

    # Start the server and rebuild the Agent.
    await self._mcp_manager.start_server(name, config)
    if self._agent_factory is not None:
      self._agent_factory.rebuild()

    # Update the in-memory config so /config reload sees it.
    self._config.mcp_servers[name] = config

    logger.info("mcp_server_added", name=name, status="connected")
    return self._mcp_manager.get_status(name)

  async def shutdown(self) -> None:
    """Graceful shutdown — stop MCP servers and clean up resources."""
    if self._config_manager is not None:
      self._config_manager.remove_listener(self._on_config_changed)
    if self._session_manager is not None:
      await self._session_manager.close()
    await self._mcp_manager.stop_all()
    logger.info("orchestrator_shutdown")
    self._initialized = False
