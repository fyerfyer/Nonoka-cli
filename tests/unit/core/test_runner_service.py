"""Tests for persisted runtime-policy maintenance in RunnerService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nonoka.core.runtime import RuntimeLimits, SessionRuntimeState, TerminalReason, Termination
from nonoka.core.session import SessionState, SessionStatus

from nonoka_cli.core.runner_service import RunnerService


async def test_refresh_persisted_session_limits_reopens_a_turn_budget_checkpoint() -> None:
  state = SessionState(
    session_id="session-1",
    status=SessionStatus.FAILED,
    runtime_state=SessionRuntimeState.create(RuntimeLimits(max_model_turns=5)),
  )
  state.runtime_state.termination = Termination(
    reason=TerminalReason.TURN_BUDGET_EXHAUSTED,
    message="Max turns (5) exceeded",
  )
  checkpoint_store = MagicMock()
  checkpoint_store.load_session = AsyncMock(return_value=state)
  checkpoint_store.save_session = AsyncMock()
  service = RunnerService(MagicMock(checkpoint_store=checkpoint_store))
  agent = SimpleNamespace(
    runtime_limits=RuntimeLimits(),
    max_turns=100,
    max_steps=20,
    default_timeout=None,
    completion_contract=None,
  )

  assert await service.refresh_persisted_session_limits(session_id="session-1", agent=agent) is True

  assert state.runtime_state.limits.max_model_turns == 100
  assert state.runtime_state.termination is None
  assert state.status == SessionStatus.PAUSED
  checkpoint_store.save_session.assert_awaited_once_with("session-1", state)


async def test_refresh_persisted_session_limits_keeps_cancellations_terminal() -> None:
  state = SessionState(
    session_id="session-1",
    status=SessionStatus.CANCELLED,
    runtime_state=SessionRuntimeState.create(RuntimeLimits(max_model_turns=5)),
  )
  state.runtime_state.termination = Termination(
    reason=TerminalReason.CANCELLED,
    message="Cancelled",
  )
  checkpoint_store = MagicMock()
  checkpoint_store.load_session = AsyncMock(return_value=state)
  checkpoint_store.save_session = AsyncMock()
  service = RunnerService(MagicMock(checkpoint_store=checkpoint_store))
  agent = SimpleNamespace(
    runtime_limits=RuntimeLimits(),
    max_turns=100,
    max_steps=20,
    default_timeout=None,
    completion_contract=None,
  )

  await service.refresh_persisted_session_limits(session_id="session-1", agent=agent)

  assert state.runtime_state.termination.reason == TerminalReason.CANCELLED
  assert state.status == SessionStatus.CANCELLED
