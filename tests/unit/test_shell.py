"""Tests for REPL shell layer."""

from __future__ import annotations

import asyncio
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.shell.repl import REPL
from nonoka_cli.ui.renderer import Renderer


class TestREPLCommandHandling:
  """Tests for REPL command routing."""

  @pytest.fixture
  def mock_orchestrator(self):
    orch = MagicMock(spec=Orchestrator)
    orch.session_id = "test-session"
    orch.new_session.return_value = "new-session-id"
    orch.execute = AsyncMock(return_value=async_event_iter([]))
    return orch

  @pytest.fixture
  def repl(self, mock_orchestrator):
    return REPL(mock_orchestrator)

  @pytest.mark.asyncio
  async def test_exit_command_stops_repl(self, repl):
    inputs = iter(["/exit"])
    with patch.object(repl, "_read_input", side_effect=lambda: asyncio.Future().set_result(next(inputs)) or asyncio.Future()):
      # Use a simpler approach: mock _read_input to return /exit
      pass

    # Direct test of command handler
    repl._running = True
    await repl._handle_command("/exit")
    assert repl._running is False

  @pytest.mark.asyncio
  async def test_quit_command_stops_repl(self, repl):
    repl._running = True
    await repl._handle_command("/quit")
    assert repl._running is False

  @pytest.mark.asyncio
  async def test_new_command_calls_orchestrator(self, repl, mock_orchestrator):
    await repl._handle_command("/new")
    mock_orchestrator.new_session.assert_called_once()

  @pytest.mark.asyncio
  async def test_help_command_prints_help(self, repl, capsys):
    await repl._handle_command("/help")
    captured = capsys.readouterr()
    assert "Available commands" in captured.out
    assert "/exit" in captured.out
    assert "/new" in captured.out

  @pytest.mark.asyncio
  async def test_unknown_command_shows_error(self, repl, capsys):
    await repl._handle_command("/unknown")
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out
    assert "/unknown" in captured.out

  @pytest.mark.asyncio
  async def test_command_with_args(self, repl, capsys):
    # Commands with extra args should still be routed correctly
    await repl._handle_command("/exit now")
    assert repl._running is False


class TestREPLPromptHandling:
  """Tests for REPL prompt execution."""

  @pytest.fixture
  def mock_orchestrator(self):
    orch = MagicMock(spec=Orchestrator)
    orch.execute = AsyncMock(return_value=async_event_iter([]))
    return orch

  @pytest.fixture
  def repl(self, mock_orchestrator):
    return REPL(mock_orchestrator)

  @pytest.mark.asyncio
  async def test_plain_prompt_calls_execute(self, repl, mock_orchestrator):
    await repl._handle_prompt("What's the weather?")
    mock_orchestrator.execute.assert_called_once_with("What's the weather?")

  @pytest.mark.asyncio
  async def test_prompt_with_special_chars(self, repl, mock_orchestrator):
    await repl._handle_prompt("Hello! How are you? 🌟")
    mock_orchestrator.execute.assert_called_once_with("Hello! How are you? 🌟")


class TestREPLInputReading:
  """Tests for REPL input handling."""

  @pytest.fixture
  def repl(self):
    orch = MagicMock(spec=Orchestrator)
    return REPL(orch)

  @pytest.mark.asyncio
  async def test_read_input_strips_whitespace(self, repl):
    with patch("asyncio.get_event_loop") as mock_loop:
      future = asyncio.Future()
      future.set_result("  hello world  ")
      mock_loop.return_value.run_in_executor.return_value = future
      result = await repl._read_input()
      assert result == "hello world"

  @pytest.mark.asyncio
  async def test_read_input_raises_eof_on_none(self, repl):
    with patch("asyncio.get_event_loop") as mock_loop:
      future = asyncio.Future()
      future.set_result(None)
      mock_loop.return_value.run_in_executor.return_value = future
      with pytest.raises(EOFError):
        await repl._read_input()


class TestREPLInterrupt:
  """Tests for REPL interrupt handling."""

  @pytest.fixture
  def repl(self):
    orch = MagicMock(spec=Orchestrator)
    renderer = MagicMock(spec=Renderer)
    return REPL(orch, renderer)

  @pytest.mark.asyncio
  async def test_interrupt_cancels_current_task(self, repl):
    repl._current_task = asyncio.create_task(asyncio.sleep(10))
    await repl.interrupt()
    assert repl._current_task.cancelled()

  @pytest.mark.asyncio
  async def test_interrupt_noop_when_no_task(self, repl):
    repl._current_task = None
    # Should not raise
    await repl.interrupt()

  @pytest.mark.asyncio
  async def test_interrupt_clears_output(self, repl):
    repl._current_task = asyncio.create_task(asyncio.sleep(10))
    await repl.interrupt()
    repl._renderer.clear_current_output.assert_called_once()


async def async_event_iter(events):
  """Helper to create an async iterator from a list."""
  for e in events:
    yield e
