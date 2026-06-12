"""Shell command parsing and routing framework.

Provides a decorator-based command registry and a router that dispatches
``/command arg1 arg2`` inputs to the appropriate handler.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

CommandHandler = Callable[["CommandContext", list[str]], Awaitable[Any]]


@dataclasses.dataclass
class CommandContext:
  """Context passed to every command handler.

  This gives commands access to the orchestrator and renderer without
  hard-coding those dependencies into the command registry.
  """

  orchestrator: Any
  renderer: Any | None = None


@dataclasses.dataclass
class CommandInfo:
  """Metadata for a registered command."""

  name: str
  handler: CommandHandler
  description: str
  usage: str | None = None
  aliases: tuple[str, ...] = ()


class CommandRegistry:
  """Registry for internal CLI commands.

  Commands are registered with the ``@command`` decorator and later
  dispatched by the router. Registration is case-insensitive.
  """

  def __init__(self) -> None:
    self._commands: dict[str, CommandInfo] = {}

  def register(
    self,
    name: str,
    handler: CommandHandler,
    *,
    description: str = "",
    usage: str | None = None,
    aliases: tuple[str, ...] = (),
  ) -> CommandInfo:
    """Register a command handler.

    Args:
      name: Primary command name (without the leading ``/``).
      handler: Async callable receiving context and args.
      description: Short description shown in /help.
      usage: Optional usage string (e.g., "<model>").
      aliases: Alternative names for the same command.

    Returns:
      The CommandInfo metadata.
    """
    info = CommandInfo(
      name=name,
      handler=handler,
      description=description,
      usage=usage,
      aliases=aliases,
    )

    self._commands[name.lower()] = info
    for alias in aliases:
      self._commands[alias.lower()] = info

    return info

  def get(self, name: str) -> CommandInfo | None:
    """Look up a command by name (case-insensitive)."""
    return self._commands.get(name.lower())

  def names(self) -> list[str]:
    """Return all registered primary command names, sorted."""
    seen: set[str] = set()
    primary: list[str] = []
    for info in self._commands.values():
      if info.name not in seen:
        primary.append(info.name)
        seen.add(info.name)
    return sorted(primary)

  def all(self) -> list[CommandInfo]:
    """Return all unique command metadata objects."""
    seen: set[str] = set()
    result: list[CommandInfo] = []
    for info in self._commands.values():
      if info.name not in seen:
        result.append(info)
        seen.add(info.name)
    return result


class CommandRouter:
  """Routes raw ``/command ...`` input to registered handlers."""

  def __init__(self, registry: CommandRegistry | None = None) -> None:
    self.registry = registry or CommandRegistry()

  def parse(self, raw: str) -> tuple[str, list[str]]:
    """Parse raw command input into command name and argument list.

    Args:
      raw: The full command string (e.g. ``/model gpt-4o``).

    Returns:
      A tuple of (command_name, args). Leading ``/`` is stripped.
    """
    text = raw.strip()
    if text.startswith("/"):
      text = text[1:]

    parts = text.split()
    if not parts:
      return "", []

    return parts[0].lower(), parts[1:]

  async def dispatch(self, ctx: CommandContext, raw: str) -> Any:
    """Dispatch a command string to its handler.

    Args:
      ctx: Command context.
      raw: Full command string.

    Returns:
      Whatever the command handler returns.

    Raises:
      UnknownCommandError: If the command is not registered.
    """
    name, args = self.parse(raw)
    if not name:
      return None

    info = self.registry.get(name)
    if info is None:
      from nonoka_cli.utils.errors import UnknownCommandError
      raise UnknownCommandError(name)

    return await info.handler(ctx, args)


def command(
  name: str,
  *,
  description: str = "",
  usage: str | None = None,
  aliases: tuple[str, ...] = (),
) -> Callable[[CommandHandler], CommandHandler]:
  """Decorator to register a command handler.

  Example::

    @command("exit", description="Exit the CLI", aliases=("quit",))
    async def cmd_exit(ctx: CommandContext, args: list[str]) -> None:
      ctx.orchestrator.shutdown()
  """

  def decorator(handler: CommandHandler) -> CommandHandler:
    _global_registry.register(
      name,
      handler,
      description=description,
      usage=usage,
      aliases=aliases,
    )
    return handler

  return decorator


# Global registry used by the ``@command`` decorator.
_global_registry = CommandRegistry()


def get_global_registry() -> CommandRegistry:
  """Return the global command registry populated by ``@command``."""
  return _global_registry


def create_router(registry: CommandRegistry | None = None) -> CommandRouter:
  """Create a command router backed by *registry*.

  If no registry is provided, the global registry is used.
  """
  return CommandRouter(registry or get_global_registry())
