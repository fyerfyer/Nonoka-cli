"""Agent factory — builds nonoka Agent from CLI configuration."""

from __future__ import annotations

from typing import Any

import structlog

from nonoka import Agent, AgentBuilder
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.utils.errors import AgentBuildError

logger = structlog.get_logger("nonoka_cli.core")


class AgentFactory:
  """Builds nonoka Agent instances from CLI configuration.

  TODO: Integrate MCP tools, local tools, and skills.
  Currently supports model and system_prompt only.
  """

  def __init__(self, config: CLIConfig):
    self._config = config
    self._agent: Agent | None = None

  @property
  def config(self) -> CLIConfig:
    """Current configuration."""
    return self._config

  def build(self) -> Agent:
    """Build (or rebuild) an Agent from the current configuration.

    Returns:
      A nonoka Agent instance.

    Raises:
      AgentBuildError: If model is not configured.
    """
    if not self._config.model:
      raise AgentBuildError("No model configured. Set 'model' in config.yaml.")

    logger.info(
      "building_agent",
      model=self._config.model,
      system_prompt_length=len(self._config.system_prompt),
    )

    self._agent = (
      AgentBuilder()
      .model(self._config.model)
      .system_prompt(self._config.system_prompt)
      .max_turns(20)
      .build()
    )
    return self._agent

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
