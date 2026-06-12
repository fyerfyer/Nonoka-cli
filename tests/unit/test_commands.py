"""Tests for the shell command routing framework."""

from __future__ import annotations

import pytest

from nonoka_cli.shell.commands import (
  CommandContext,
  CommandRegistry,
  CommandRouter,
)
from nonoka_cli.utils.errors import UnknownCommandError


class TestCommandRegistry:
  """Tests for CommandRegistry."""

  @pytest.fixture
  def registry(self):
    return CommandRegistry()

  @pytest.mark.asyncio
  async def test_register_and_lookup(self, registry):
    async def handler(ctx, args):
      return "handled"

    registry.register("test", handler, description="A test command")
    info = registry.get("test")
    assert info is not None
    assert info.name == "test"
    assert await info.handler(None, []) == "handled"

  def test_lookup_is_case_insensitive(self, registry):
    registry.register("Test", lambda ctx, args: None, description="Mixed case")
    assert registry.get("test") is not None
    assert registry.get("TEST") is not None

  def test_aliases_share_info(self, registry):
    registry.register(
      "exit",
      lambda ctx, args: None,
      description="Exit",
      aliases=("quit", "q"),
    )
    assert registry.get("quit").name == "exit"
    assert registry.get("q").name == "exit"

  def test_names_returns_primary_names(self, registry):
    registry.register("alpha", lambda ctx, args: None, description="A")
    registry.register("beta", lambda ctx, args: None, description="B")
    assert registry.names() == ["alpha", "beta"]

  def test_names_dedupes_aliases(self, registry):
    registry.register("exit", lambda ctx, args: None, aliases=("quit",))
    assert registry.names() == ["exit"]

  def test_all_returns_unique_commands(self, registry):
    registry.register("exit", lambda ctx, args: None, aliases=("quit",))
    registry.register("new", lambda ctx, args: None)
    assert len(registry.all()) == 2


class TestCommandRouter:
  """Tests for CommandRouter."""

  @pytest.fixture
  def router(self):
    async def echo_handler(ctx, args):
      return args

    registry = CommandRegistry()
    registry.register("echo", echo_handler, description="Echo args")
    return CommandRouter(registry)

  def test_parse_command_with_args(self, router):
    name, args = router.parse("/echo hello world")
    assert name == "echo"
    assert args == ["hello", "world"]

  def test_parse_strips_leading_slash(self, router):
    name, args = router.parse("echo hello")
    assert name == "echo"

  def test_parse_empty_command(self, router):
    name, args = router.parse("/")
    assert name == ""
    assert args == []

  def test_parse_ignores_extra_whitespace(self, router):
    name, args = router.parse("  /echo   a   b  ")
    assert name == "echo"
    assert args == ["a", "b"]

  @pytest.mark.asyncio
  async def test_dispatch_calls_handler(self, router):
    ctx = CommandContext(orchestrator=None)
    result = await router.dispatch(ctx, "/echo hello world")
    assert result == ["hello", "world"]

  @pytest.mark.asyncio
  async def test_dispatch_unknown_raises(self, router):
    ctx = CommandContext(orchestrator=None)
    with pytest.raises(UnknownCommandError, match="Unknown command"):
      await router.dispatch(ctx, "/missing")

  @pytest.mark.asyncio
  async def test_dispatch_empty_returns_none(self, router):
    ctx = CommandContext(orchestrator=None)
    result = await router.dispatch(ctx, "/")
    assert result is None
