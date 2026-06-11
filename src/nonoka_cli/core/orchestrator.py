"""Orchestrator — coordinates config, agent, runner, and execution."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from nonoka import Runner
from nonoka.core.runner import StreamEvent

from nonoka_cli.config.loader import ConfigLoader
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

  TODO: Add MCP, Skill, session management, HITL.
  """

  def __init__(self, config: CLIConfig | None = None):
    """Initialize the orchestrator.

    Args:
      config: Pre-loaded configuration. If None, will be loaded on initialize().
    """
    self._config = config
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
  def session_id(self) -> str:
    """Current session identifier."""
    return self._session_id

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
        self._config = ConfigLoader.load(config_path)
      except ConfigError:
        raise
      except Exception as exc:
        raise ConfigError(f"Failed to load configuration: {exc}") from exc

    self._agent_factory = AgentFactory(self._config)
    agent = self._agent_factory.build()

    self._runner = Runner()

    logger.info(
      "orchestrator_initialized",
      model=self._config.model,
      session_id=self._session_id,
    )
    self._initialized = True

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

  async def shutdown(self) -> None:
    """Graceful shutdown — clean up resources."""
    logger.info("orchestrator_shutdown")
    self._initialized = False
