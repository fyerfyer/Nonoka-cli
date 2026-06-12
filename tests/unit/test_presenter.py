"""Tests for the rich-based UI presenter."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.ui.presenter import UIPresenter


@pytest.fixture
def presenter():
  output = StringIO()
  console = Console(file=output, force_terminal=False, width=200)
  return UIPresenter(console)


class TestUIPresenterGeneral:
  """Tests for general presenter output methods."""

  def test_success_output(self, presenter):
    presenter.success("Done")
    assert "✓ Done" in presenter.console.file.getvalue()

  def test_error_output(self, presenter):
    presenter.error("Something went wrong")
    output = presenter.console.file.getvalue()
    assert "Error" in output
    assert "Something went wrong" in output

  def test_warning_output(self, presenter):
    presenter.warning("Careful")
    assert "Careful" in presenter.console.file.getvalue()

  def test_info_output(self, presenter):
    presenter.info("Note")
    assert "Note" in presenter.console.file.getvalue()


class TestUIPresenterLifecycle:
  """Tests for banner and goodbye output."""

  def test_banner_shows_model_and_config(self, presenter):
    presenter.show_banner(model="deepseek-chat", config_path="/tmp/config.yaml")
    output = presenter.console.file.getvalue()
    assert "nonoka-cli" in output
    assert "deepseek-chat" in output
    assert "/tmp/config.yaml" in output

  def test_goodbye_output(self, presenter):
    presenter.show_goodbye()
    assert "Goodbye" in presenter.console.file.getvalue()


class TestUIPresenterCommands:
  """Tests for command feedback methods."""

  def test_new_session_output(self, presenter):
    presenter.show_new_session("session-123")
    output = presenter.console.file.getvalue()
    assert "New session" in output
    assert "session-123" in output

  def test_model_switched_output(self, presenter):
    presenter.show_model_switched("gpt-4o-mini", "session-123")
    output = presenter.console.file.getvalue()
    assert "gpt-4o-mini" in output
    assert "session-123" in output

  def test_current_model_output(self, presenter):
    presenter.show_current_model("gpt-4o")
    output = presenter.console.file.getvalue()
    assert "gpt-4o" in output
    assert "/model" in output

  def test_config_reloaded_output(self, presenter):
    config = CLIConfig(model="gpt-4o", system_prompt="Updated prompt.")
    presenter.show_config_reloaded(config)
    output = presenter.console.file.getvalue()
    assert "Config reloaded" in output
    assert "gpt-4o" in output
    assert "15" in output  # len("Updated prompt.") == 15

  def test_config_opened_output(self, presenter):
    presenter.show_config_opened("/tmp/config.yaml", "vim")
    output = presenter.console.file.getvalue()
    assert "/tmp/config.yaml" in output
    assert "vim" in output


class TestUIPresenterHelp:
  """Tests for help display methods."""

  @pytest.fixture
  def registry(self):
    registry = CommandRegistry()
    registry.register("exit", lambda ctx, args: None, aliases=("quit",))
    registry.register(
      "model",
      lambda ctx, args: None,
      usage="<model>",
      description="Switch the active LLM model",
    )
    return registry

  def test_show_help_lists_commands(self, presenter, registry):
    presenter.show_help(registry)
    output = presenter.console.file.getvalue()
    assert "Available Commands" in output
    assert "/exit" in output
    assert "/model" in output
    assert "Switch the active LLM model" in output

  def test_show_command_help(self, presenter):
    from nonoka_cli.shell.commands import CommandInfo
    info = CommandInfo(
      name="model",
      handler=lambda ctx, args: None,
      description="Switch model",
      usage="<model>",
    )
    presenter.show_command_help(info)
    output = presenter.console.file.getvalue()
    assert "/model" in output
    assert "Switch model" in output

  def test_show_unknown_command(self, presenter):
    presenter.show_unknown_command("missing")
    output = presenter.console.file.getvalue()
    assert "Unknown command" in output
    assert "/missing" in output
    assert "/help" in output
