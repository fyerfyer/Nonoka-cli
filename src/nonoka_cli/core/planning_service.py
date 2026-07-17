"""Planning service for nonoka-cli.

Wires planner/executor configuration (``agents.planner``) to the orchestrator.
The actual planner implementation has been removed from nonoka-agent in 1.3.4;
nonoka-cli will provide its own LLM-based planner here in a future release.
For now the service reports itself as disabled when invoked so the orchestrator
falls back to single-agent execution.
"""

from __future__ import annotations

from pathlib import Path

from nonoka_cli.config.models import AgentsConfig, CLIConfig


class PlanningService:
  """Adapts the nonoka-agent planner tool to CLI config."""

  def __init__(
    self,
    working_dir: Path,
    config: AgentsConfig | None = None,
    default_model: str | None = None,
  ):
    self._working_dir = working_dir
    self._config = config or AgentsConfig()
    self._default_model = default_model

  @property
  def enabled(self) -> bool:
    return bool(self.planner_model)

  @property
  def planner_model(self) -> str:
    """Return the effective planner model identifier."""
    configured = self._config.planner.model
    if configured:
      return configured
    if self._default_model:
      return self._default_model
    return ""

  @property
  def executor_model(self) -> str:
    """Return the effective executor model identifier."""
    configured = self._config.executor.model
    if configured:
      return configured
    if self._default_model:
      return self._default_model
    return ""

  @property
  def planner_max_turns(self) -> int:
    return self._config.planner.max_turns

  @property
  def executor_max_turns(self) -> int:
    return self._config.executor.max_turns

  async def plan(self, task: str, max_steps: int = 10) -> str:
    """Generate a structured plan for *task*.

    Returns a human-readable plan or an error message. The planner tool has
    been removed from nonoka-agent in 1.3.4; a CLI-native planner will replace
    this stub in a future release.
    """
    if not self.enabled:
      return "Planning is disabled: no planner model configured."
    return (
      "Planning is disabled: the built-in planner tool was removed from "
      "nonoka-agent 1.3.4. Configure a planner model to opt into the future "
      "CLI-native planner implementation."
    )


def build_planning_service(
  working_dir: Path,
  config: CLIConfig | None = None,
) -> PlanningService:
  """Factory for creating a PlanningService from CLI configuration."""
  agents_config = config.agents if config is not None else AgentsConfig()
  default_model = config.model if config is not None else None
  return PlanningService(
    working_dir=working_dir,
    config=agents_config,
    default_model=default_model,
  )
