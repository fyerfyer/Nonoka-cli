"""Tests for tab completion support."""

from __future__ import annotations

import pytest

from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.shell.completer import CommandCompleter, build_completer


class TestCommandCompleter:
  """Tests for CommandCompleter."""

  @pytest.fixture
  def registry(self):
    registry = CommandRegistry()
    registry.register("exit", lambda ctx, args: None, aliases=("quit",))
    registry.register("new", lambda ctx, args: None)
    registry.register("model", lambda ctx, args: None)
    registry.register("config", lambda ctx, args: None)
    registry.register("help", lambda ctx, args: None)
    return registry

  @pytest.mark.asyncio
  async def test_completes_full_command(self, registry):
    completer = build_completer(registry, get_line_buffer=lambda: "/ex")
    assert completer("", 0) == "/exit"
    assert completer("", 1) is None

  @pytest.mark.asyncio
  async def test_no_completion_without_leading_slash(self, registry):
    completer = build_completer(registry, get_line_buffer=lambda: "ex")
    assert completer("", 0) is None

  @pytest.mark.asyncio
  async def test_completes_multiple_candidates(self, registry):
    completer = build_completer(registry, get_line_buffer=lambda: "/")
    results = []
    state = 0
    while True:
      candidate = completer("", state)
      if candidate is None:
        break
      results.append(candidate)
      state += 1

    assert results == ["/config", "/exit", "/help", "/model", "/new"]

  @pytest.mark.asyncio
  async def test_no_match_returns_none(self, registry):
    completer = build_completer(registry, get_line_buffer=lambda: "/xyz")
    assert completer("", 0) is None

  @pytest.mark.asyncio
  async def test_completes_partial_prefix(self, registry):
    completer = build_completer(registry, get_line_buffer=lambda: "/n")
    assert completer("", 0) == "/new"
    assert completer("", 1) is None
