"""Shell / REPL layer for nonoka-cli."""

from nonoka_cli.shell.commands import (
  CommandContext,
  CommandInfo,
  CommandRegistry,
  CommandRouter,
  command,
  create_router,
  get_global_registry,
)
from nonoka_cli.shell.completer import CommandCompleter, setup_completion
from nonoka_cli.shell.prompt_input import PromptInput
from nonoka_cli.shell.ptk_completer import PTCommandCompleter

# REPL is imported lazily to avoid a circular import with ui.presenter,
# which imports CommandInfo / CommandRegistry from this package.
REPL = None


def __getattr__(name: str):
  if name == "REPL":
    from nonoka_cli.shell.repl import REPL as _REPL
    return _REPL
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
  "REPL",
  "CommandContext",
  "CommandInfo",
  "CommandRegistry",
  "CommandRouter",
  "command",
  "create_router",
  "get_global_registry",
  "CommandCompleter",
  "setup_completion",
  "PromptInput",
  "PTCommandCompleter",
]
