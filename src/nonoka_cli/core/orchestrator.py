"""Orchestrator — coordinates config, agent, runner, and execution."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from nonoka import Runner
from nonoka.core.runner import StreamEvent

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.utils.errors import ConfigError, OrchestratorError

logger = structlog.get_logger("nonoka_cli.core")


class Orchestrator:
  """Orchestration layer — the bridge between Shell and nonoka kernel.

  Responsibilities:
  - Load configuration
  - Build Agent and Runner
  - Execute prompts via nonoka's streaming ReAct API
  - Manage a single session_id
  - Support model switching and config hot-reload while preserving context

  TODO: Add MCP, Skill, session management, HITL.
  """

  def __init__(
    self,
    config: CLIConfig | None = None,
    config_manager: ConfigManager | None = None,
  ):
    """Initialize the orchestrator.

    Args:
      config: Pre-loaded configuration. If None, will be loaded on initialize().
      config_manager: Optional ConfigManager for hot-reload support.
    """
    self._config = config
    self._config_manager = config_manager
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
  def agent_factory(self) -> AgentFactory | None:
    """Current AgentFactory, if initialized."""
    return self._agent_factory

  async def initialize(self, config_path: Path | str | None = None) -> None:
    """Load config, build agent, and create runner.

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

    self._agent_factory = AgentFactory(self._config)
    agent = self._agent_factory.build()

    self._runner = Runner()

    # Subscribe to config changes so system_prompt etc. take effect
    # on the next Agent rebuild (model changes are handled explicitly).
    if self._config_manager is not None:
      self._config_manager.on_change(self._on_config_changed)

    logger.info(
      "orchestrator_initialized",
      model=self._config.model,
      session_id=self._session_id,
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

  def new_session(self) -> str:
    """Create a new session, discarding the old one.

    Returns:
      The new session_id.
    """
    self._session_id = str(uuid.uuid4())
    logger.info("new_session_created", session_id=self._session_id)
    return self._session_id

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

  async def shutdown(self) -> None:
    """Graceful shutdown — clean up resources."""
    if self._config_manager is not None:
      self._config_manager.remove_listener(self._on_config_changed)
    logger.info("orchestrator_shutdown")
    self._initialized = False
