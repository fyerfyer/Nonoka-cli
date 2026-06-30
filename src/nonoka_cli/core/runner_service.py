"""Runner execution service for nonoka-cli."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog
from nonoka import Agent, Runner
from nonoka.core.runner import StreamEvent

from nonoka_cli.utils.errors import OrchestratorError

logger = structlog.get_logger("nonoka_cli.core.runner")


class RunnerService:
  """Thin wrapper around ``nonoka.Runner.run_react_stream``.

  Encapsulates the execution lifecycle so ``Orchestrator`` does not need to
  interact directly with the Runner internals.
  """

  def __init__(self, runner: Runner):
    """Args:
      runner: Configured nonoka Runner instance.
    """
    self._runner = runner

  @property
  def runner(self) -> Runner:
    """Underlying nonoka Runner."""
    return self._runner

  async def run(
    self,
    agent: Agent,
    prompt: str,
    deps: Any,
    session_id: str,
  ) -> AsyncIterator[StreamEvent]:
    """Execute a prompt against *agent* and yield streaming events.

    Args:
      agent: The nonoka Agent to run.
      prompt: The user's input text.
      deps: Runtime dependencies passed to tools via ``RunContext.deps``.
      session_id: Session identifier for checkpoint persistence.

    Yields:
      StreamEvent objects from nonoka's ReAct streaming API.

    Raises:
      OrchestratorError: If execution fails.
    """
    try:
      async for event in self._runner.run_react_stream(
        agent, prompt, deps=deps, session_id=session_id
      ):
        yield event
    except Exception as exc:
      logger.error("runner_execution_failed", error=str(exc))
      raise OrchestratorError(f"Execution failed: {exc}") from exc
