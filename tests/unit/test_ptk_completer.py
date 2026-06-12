"""Tests for the prompt-toolkit command completer."""

from __future__ import annotations

import pytest
from prompt_toolkit.document import Document

from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.shell.ptk_completer import PTCommandCompleter


@pytest.fixture
def registry():
  """Return a registry with a few sample commands."""
  reg = CommandRegistry()
  reg.register("exit", lambda ctx, args: None, description="Exit")
  reg.register("session", lambda ctx, args: None, description="Session")
  reg.register("session-list", lambda ctx, args: None, description="List")
  return reg


class TestPTCommandCompleter:
  """Tests for PTCommandCompleter."""

  def test_completes_full_command(self, registry: CommandRegistry):
    completer = PTCommandCompleter(registry)
    doc = Document("/ex", cursor_position=3)

    completions = list(completer.get_completions(doc, None))

    assert len(completions) == 1
    assert completions[0].text == "/exit"

  def test_completes_multiple_candidates(self, registry: CommandRegistry):
    completer = PTCommandCompleter(registry)
    doc = Document("/session", cursor_position=8)

    completions = list(completer.get_completions(doc, None))
    texts = {c.text for c in completions}

    assert "/session" in texts
    assert "/session-list" in texts

  def test_slash_lists_all_commands(self, registry: CommandRegistry):
    completer = PTCommandCompleter(registry)
    doc = Document("/", cursor_position=1)

    completions = list(completer.get_completions(doc, None))
    texts = {c.text for c in completions}

    assert texts == {"/exit", "/session", "/session-list"}

  def test_no_completion_without_slash(self, registry: CommandRegistry):
    completer = PTCommandCompleter(registry)
    doc = Document("ex", cursor_position=2)

    completions = list(completer.get_completions(doc, None))

    assert completions == []

  def test_start_position_replaces_entire_prefix(self, registry: CommandRegistry):
    completer = PTCommandCompleter(registry)
    doc = Document("/ex", cursor_position=3)

    completions = list(completer.get_completions(doc, None))

    assert completions[0].start_position == -3
