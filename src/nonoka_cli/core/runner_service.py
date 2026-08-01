"""Runner execution service for nonoka-cli."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog
from nonoka import Agent, Runner
from nonoka.core.runner import StreamEvent
from nonoka.core.runtime import RuntimeLimits, TerminalReason
from nonoka.core.session import SessionStatus

from nonoka_cli.utils.errors import OrchestratorError

logger = structlog.get_logger("nonoka_cli.core.runner")

_RELOADABLE_TERMINATIONS = {
  TerminalReason.TURN_BUDGET_EXHAUSTED,
  TerminalReason.TOOL_BUDGET_EXHAUSTED,
  TerminalReason.CONTEXT_BUDGET_EXHAUSTED,
  TerminalReason.DEADLINE_EXCEEDED,
  TerminalReason.MODEL_TIMEOUT,
  TerminalReason.TOKEN_BUDGET_EXHAUSTED,
  TerminalReason.COST_BUDGET_EXHAUSTED,
  TerminalReason.COST_UNAVAILABLE,
}


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

  async def refresh_persisted_session_limits(self, *, session_id: str, agent: Agent) -> bool:
    """Apply an agent's current runtime policy to a saved session checkpoint.

    Host tool calls pause the ReAct loop and serialize their runtime limits.
    Those serialized limits deliberately win on resume, so an agent rebuild by
    itself cannot apply edited configuration after ``/reload``. Cancellation
    and execution-policy failures are intentionally never cleared here.
    """
    state = await self._runner.checkpoint_store.load_session(session_id)
    if state is None or state.runtime_state is None:
      return False

    configured = getattr(agent, "runtime_limits", None)
    limits = (
      configured.model_copy(deep=True)
      if isinstance(configured, RuntimeLimits)
      else RuntimeLimits()
    )
    # Mirror Session.__init__'s effective-limit calculation. Agent builders
    # leave these fields unset whenever their persisted configuration owns it.
    if limits.max_model_turns is None:
      limits.max_model_turns = getattr(agent, "max_turns", None)
    if limits.max_tool_calls is None:
      limits.max_tool_calls = getattr(agent, "max_steps", None)
    if limits.model_timeout_seconds is None:
      limits.model_timeout_seconds = getattr(agent, "default_timeout", None)

    previous_limits = state.runtime_state.limits
    state.runtime_state.limits = limits
    state.completion_contract = getattr(agent, "completion_contract", None)

    termination = state.runtime_state.termination
    if previous_limits != limits and termination and termination.reason in _RELOADABLE_TERMINATIONS:
      state.runtime_state.termination = None
      # The next resume will set RUNNING. Mark the checkpoint accurately while
      # it waits for the host to return its pending external tool result.
      if state.status == SessionStatus.FAILED:
        state.status = SessionStatus.PAUSED
      state.end_time = None

    await self._runner.checkpoint_store.save_session(session_id, state)
    return True

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

  async def resume_approval(
    self,
    agent: Agent,
    deps: Any,
    session_id: str,
    approvals: dict[str, dict[str, Any]],
  ) -> AsyncIterator[StreamEvent]:
    """Resume a session paused for tool-call approvals.

    Args:
      agent: The nonoka Agent to run.
      deps: Runtime dependencies passed to tools.
      session_id: Session identifier for checkpoint persistence.
      approvals: Mapping from tool_call_id to decision dict with
        ``approved: bool`` and optional ``modified_args``.

    Yields:
      StreamEvent objects from the resumed ReAct loop.
    """
    try:
      async for event in self._runner.resume_approval(
        agent, deps=deps, session_id=session_id, approvals=approvals
      ):
        yield event
    except Exception as exc:
      logger.error("approval_resume_failed", error=str(exc))
      raise OrchestratorError(f"Approval resume failed: {exc}") from exc

  async def resume_external_tools(
    self,
    agent: Agent,
    deps: Any,
    session_id: str,
    results: dict[str, Any],
  ) -> AsyncIterator[StreamEvent]:
    """Resume a session paused for external tool execution.

    Args:
      agent: The nonoka Agent to run.
      deps: Runtime dependencies passed to tools.
      session_id: Session identifier for checkpoint persistence.
      results: Mapping from tool_call_id to the result returned by the
        external host.

    Yields:
      StreamEvent objects from the resumed ReAct loop.
    """
    try:
      async for event in self._runner.resume_external_tools(
        agent, deps=deps, session_id=session_id, results=results
      ):
        yield event
    except Exception as exc:
      logger.error("external_tool_resume_failed", error=str(exc))
      raise OrchestratorError(f"External tool resume failed: {exc}") from exc
