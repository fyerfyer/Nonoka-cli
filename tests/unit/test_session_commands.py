"""Tests for /session REPL commands."""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nonoka_cli.config.manager import ConfigManager
from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.shell.repl import REPL
from nonoka_cli.sessions.models import SessionInfo
from nonoka_cli.ui.presenter import UIPresenter
from nonoka_cli.ui.renderer import Renderer
from rich.console import Console


def _sample_session(**overrides) -> SessionInfo:
  defaults = {
    "session_id": "test-session-id",
    "name": "Test Session",
    "model": "gpt-4o",
    "created_at": datetime(2026, 6, 12, 10, 0, 0),
    "last_active": datetime(2026, 6, 12, 10, 30, 0),
    "message_count": 5,
    "metadata": {},
  }
  defaults.update(overrides)
  return SessionInfo(**defaults)


class TestSessionCommandHandling:
  """Tests for /session sub-command routing."""

  @pytest.fixture
  def mock_orchestrator(self):
    config = CLIConfig(model="gpt-4o", system_prompt="Test.")
    config_manager = MagicMock(spec=ConfigManager)
    config_manager.config_path = Path("/tmp/config.yaml")

    orch = MagicMock(spec=Orchestrator)
    orch.session_id = "test-session-id"
    orch.config = config
    orch.config_manager = config_manager
    orch.new_session = AsyncMock(return_value="new-session-id")
    orch.get_current_session = AsyncMock(return_value=_sample_session())
    orch.list_sessions = AsyncMock(return_value=[_sample_session()])
    orch.switch_session = AsyncMock(return_value=_sample_session(session_id="switched-id"))
    orch.rename_session = AsyncMock(return_value=_sample_session(name="Renamed"))
    orch.delete_session = AsyncMock()
    orch.execute = MagicMock(return_value=async_event_iter([]))
    return orch

  @pytest.fixture
  def repl(self, mock_orchestrator):
    # Capture rich output to StringIO for assertions.
    console = Console(file=StringIO(), force_terminal=False)
    presenter = UIPresenter(console=console)
    return REPL(mock_orchestrator, presenter=presenter)

  @pytest.mark.asyncio
  async def test_session_command_shows_current_info(self, repl, mock_orchestrator):
    await repl._handle_command("/session")
    mock_orchestrator.get_current_session.assert_awaited_once()

  @pytest.mark.asyncio
  async def test_session_list_shows_sessions(self, repl, mock_orchestrator):
    await repl._handle_command("/session list")
    mock_orchestrator.list_sessions.assert_awaited_once()

  @pytest.mark.asyncio
  async def test_session_switch_calls_orchestrator(self, repl, mock_orchestrator):
    await repl._handle_command("/session switch some-id")
    mock_orchestrator.switch_session.assert_awaited_once_with("some-id")

  @pytest.mark.asyncio
  async def test_session_switch_without_args_shows_error(self, repl, mock_orchestrator):
    await repl._handle_command("/session switch")
    mock_orchestrator.switch_session.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_session_rename_calls_orchestrator(self, repl, mock_orchestrator):
    await repl._handle_command("/session rename My Session")
    mock_orchestrator.rename_session.assert_awaited_once_with("My Session")

  @pytest.mark.asyncio
  async def test_session_rename_without_args_shows_error(self, repl, mock_orchestrator):
    await repl._handle_command("/session rename")
    mock_orchestrator.rename_session.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_session_delete_calls_orchestrator(self, repl, mock_orchestrator):
    await repl._handle_command("/session delete some-id")
    mock_orchestrator.delete_session.assert_awaited_once_with("some-id")

  @pytest.mark.asyncio
  async def test_session_delete_without_args_shows_error(self, repl, mock_orchestrator):
    await repl._handle_command("/session delete")
    mock_orchestrator.delete_session.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_session_unknown_subcommand_shows_error(self, repl, mock_orchestrator):
    await repl._handle_command("/session unknown")
    mock_orchestrator.list_sessions.assert_not_awaited()
    mock_orchestrator.switch_session.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_new_command_with_name(self, repl, mock_orchestrator):
    await repl._handle_command("/new My Session")
    mock_orchestrator.new_session.assert_awaited_once_with(name="My Session")

  @pytest.mark.asyncio
  async def test_new_command_without_name(self, repl, mock_orchestrator):
    await repl._handle_command("/new")
    mock_orchestrator.new_session.assert_awaited_once_with(name=None)


async def async_event_iter(events):
  """Helper to create an async iterator from a list."""
  for e in events:
    yield e
