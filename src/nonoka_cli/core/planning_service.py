"""Planning service for nonoka-cli.

Wraps the nonoka-agent ``plan_task`` tool and wires it to CLI configuration
(``agents.planner``). The planner tool itself lives in nonoka-agent so other
front-ends can reuse it; this service only adapts config and provides helpers
for the orchestrator.
"""

from __future__ import annotations

import structlog
from pathlib import Path
from typing import Any

from nonoka.core.context import RunContext
from nonoka.tools.planning import plan_task

from nonoka_cli.config.models import AgentsConfig, CLIConfig

logger = structlog.get_logger("nonoka_cli.core")


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

    Returns a human-readable plan or an error message.
    """
    if not self.enabled:
      return "Planning is disabled: no planner model configured."

    ctx = self._run_context()
    try:
      return await plan_task(
        ctx,
        task=task,
        max_steps=max_steps,
        max_turns=self.planner_max_turns,
      )
    except Exception as exc:
      logger.warning("plan_task_failed", error=str(exc))
      return f"Error generating plan: {exc}"

  def _run_context(self) -> RunContext:
    from nonoka.core.agent import Agent
    from nonoka.core.session import Session

    agent = Agent(model=self.planner_model or "planner")
    session = Session(
      session_id="planner",
      agent=agent,
      deps=_PlanningDeps(
        working_dir=str(self._working_dir),
        config=_PlannerConfigStub(model=self.planner_model),
      ),
    )
    return RunContext(session)


class _PlannerConfigStub:
  def __init__(self, model: str):
    self.model = model


class _PlanningDeps:
  def __init__(self, working_dir: str, config: Any):
    self.working_dir = working_dir
    self.config = config


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
