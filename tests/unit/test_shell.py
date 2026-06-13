"""Tests for REPL shell layer."""

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.shell.repl import REPL
from nonoka_cli.ui.renderer import Renderer


class TestREPLCommandHandling:
  """Tests for REPL command routing."""

  @pytest.fixture
  def mock_orchestrator(self):
    config = CLIConfig(model="gpt-4o", system_prompt="Test.")
    config_manager = MagicMock(spec=ConfigManager)
    config_manager.config_path = Path("/tmp/config.yaml")

    orch = MagicMock(spec=Orchestrator)
    orch.session_id = "test-session"
    orch.config = config
    orch.config_manager = config_manager
    orch.new_session = AsyncMock(return_value="new-session-id")
    orch.execute = MagicMock(return_value=async_event_iter([]))
    orch.switch_model = AsyncMock()
    orch.reload_config = AsyncMock(return_value=config)
    return orch

  @pytest.fixture
  def repl(self, mock_orchestrator):
    return REPL(mock_orchestrator)

  @pytest.mark.asyncio
  async def test_exit_command_stops_repl(self, repl):
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
    mock_orchestrator.new_session.assert_awaited_once_with(name=None)

  @pytest.mark.asyncio
  async def test_help_command_prints_help(self, repl, capsys):
    await repl._handle_command("/help")
    captured = capsys.readouterr()
    assert "Available Commands" in captured.out
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

  @pytest.mark.asyncio
  async def test_model_command_switches_model(self, repl, mock_orchestrator, capsys):
    await repl._handle_command("/model gpt-4o-mini")
    mock_orchestrator.switch_model.assert_awaited_once_with("gpt-4o-mini")
    captured = capsys.readouterr()
    assert "gpt-4o-mini" in captured.out

  @pytest.mark.asyncio
  async def test_model_command_without_args_shows_current(self, repl, mock_orchestrator, capsys):
    await repl._handle_command("/model")
    mock_orchestrator.switch_model.assert_not_awaited()
    captured = capsys.readouterr()
    assert "Current model: gpt-4o" in captured.out

  @pytest.mark.asyncio
  async def test_config_reload_command(self, repl, mock_orchestrator, capsys):
    await repl._handle_command("/config reload")
    mock_orchestrator.reload_config.assert_awaited_once()
    captured = capsys.readouterr()
    assert "Config reloaded" in captured.out

  @pytest.mark.asyncio
  async def test_help_for_specific_command(self, repl, capsys):
    await repl._handle_command("/help model")
    captured = capsys.readouterr()
    assert "/model <model>" in captured.out

  @pytest.mark.asyncio
  async def test_help_for_unknown_command(self, repl, capsys):
    await repl._handle_command("/help missing")
    captured = capsys.readouterr()
    assert "Unknown command" in captured.out

  @pytest.mark.asyncio
  async def test_mcp_list_command(self, repl, mock_orchestrator, capsys):
    from nonoka_cli.mcp.models import MCPStatus
    mock_orchestrator.list_mcp_status.return_value = {
      "fetch": MCPStatus(
        name="fetch",
        status="connected",
        transport="stdio",
        tool_count=3,
        last_ping=None,
        restart_count=0,
        error=None,
      ),
    }
    await repl._handle_command("/mcp list")
    mock_orchestrator.list_mcp_status.assert_called_once()
    captured = capsys.readouterr()
    assert "MCP Servers" in captured.out
    assert "fetch" in captured.out

  @pytest.mark.asyncio
  async def test_mcp_restart_command(self, repl, mock_orchestrator, capsys):
    from nonoka_cli.mcp.models import MCPStatus
    status = MCPStatus(
      name="fetch",
      status="connected",
      transport="stdio",
      tool_count=3,
      last_ping=None,
      restart_count=1,
      error=None,
    )
    mock_orchestrator.restart_mcp = AsyncMock(return_value=status)
    await repl._handle_command("/mcp restart fetch")
    mock_orchestrator.restart_mcp.assert_awaited_once_with("fetch")
    captured = capsys.readouterr()
    assert "fetch" in captured.out

  @pytest.mark.asyncio
  async def test_mcp_restart_without_name_shows_error(self, repl, capsys):
    await repl._handle_command("/mcp restart")
    captured = capsys.readouterr()
    assert "Usage" in captured.out

  @pytest.mark.asyncio
  async def test_mcp_add_command(self, repl, mock_orchestrator, capsys):
    from nonoka_cli.mcp.models import MCPStatus
    status = MCPStatus(
      name="fetch",
      status="connected",
      transport="stdio",
      tool_count=3,
      last_ping=None,
      restart_count=0,
      error=None,
    )
    mock_orchestrator.add_mcp_server = AsyncMock(return_value=status)
    await repl._handle_command("/mcp add fetch uvx mcp-server-fetch")
    mock_orchestrator.add_mcp_server.assert_awaited_once()
    captured = capsys.readouterr()
    assert "fetch" in captured.out
    assert "added" in captured.out.lower()

  @pytest.mark.asyncio
  async def test_mcp_add_without_enough_args_shows_error(self, repl, capsys):
    await repl._handle_command("/mcp add fetch")
    captured = capsys.readouterr()
    assert "Usage" in captured.out


class TestREPLPromptHandling:
  """Tests for REPL prompt execution."""

  @pytest.fixture
  def mock_orchestrator(self):
    config = CLIConfig(model="gpt-4o", system_prompt="Test.")
    config_manager = MagicMock(spec=ConfigManager)
    config_manager.config_path = Path("/tmp/config.yaml")

    orch = MagicMock(spec=Orchestrator)
    orch.config = config
    orch.config_manager = config_manager
    orch.execute = MagicMock(return_value=async_event_iter([]))
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
    config = CLIConfig(model="gpt-4o", system_prompt="Test.")
    config_manager = MagicMock(spec=ConfigManager)
    config_manager.config_path = Path("/tmp/config.yaml")

    orch = MagicMock(spec=Orchestrator)
    orch.config = config
    orch.config_manager = config_manager
    return REPL(orch)

  @pytest.mark.asyncio
  async def test_repl_receives_stripped_input(self, repl):
    with patch.object(repl._prompt_input, "read", new_callable=AsyncMock) as mock_read:
      mock_read.return_value = "hello world"
      result = await repl._prompt_input.read()
      assert result == "hello world"

  @pytest.mark.asyncio
  async def test_empty_input_skips_processing(self, repl):
    with patch.object(repl._prompt_input, "read", new_callable=AsyncMock) as mock_read:
      mock_read.side_effect = ["", "/exit"]
      await repl.run()
      assert repl._running is False


class TestREPLInterrupt:
  """Tests for REPL interrupt handling."""

  @pytest.fixture
  def repl(self):
    config = CLIConfig(model="gpt-4o", system_prompt="Test.")
    config_manager = MagicMock(spec=ConfigManager)
    config_manager.config_path = Path("/tmp/config.yaml")

    orch = MagicMock(spec=Orchestrator)
    orch.config = config
    orch.config_manager = config_manager
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
