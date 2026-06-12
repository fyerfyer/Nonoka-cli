"""Tab completion support for the REPL.

Uses the ``readline`` module to provide command-line completion for
internal ``/`` commands. Completion is only active when the current input
starts with ``/``.
"""

from __future__ import annotations

import logging
from typing import Callable

try:
  import readline
except ImportError:  # pragma: no cover - Windows fallback
  readline = None  # type: ignore[assignment]

from nonoka_cli.shell.commands import CommandRegistry

logger = logging.getLogger("nonoka_cli.shell")


class CommandCompleter:
  """readline completer for CLI internal commands.

  Only completes commands when the input starts with ``/``; otherwise
  returns an empty list so readline falls back to default behavior.
  """

  def __init__(
    self,
    registry: CommandRegistry,
    get_line_buffer: Callable[[], str] | None = None,
  ):
    self.registry = registry
    if get_line_buffer is not None:
      self._get_line_buffer = get_line_buffer
    elif readline is not None:
      self._get_line_buffer = readline.get_line_buffer
    else:
      self._get_line_buffer = lambda: ""

  def __call__(self, text: str, state: int) -> str | None:
    """readline completer callback.

    Args:
      text: The word under the cursor.
      state: Index of the candidate to return.

    Returns:
      The *state*-th matching completion, or None when exhausted.
    """
    line = self._get_line_buffer()
    if not line.startswith("/"):
      return None

    # Strip leading slash for matching command names
    prefix = line[1:]
    candidates = [name for name in self.registry.names() if name.startswith(prefix)]

    if state < len(candidates):
      return f"/{candidates[state]}"

    return None


def setup_completion(completer: CommandCompleter | CommandRegistry) -> None:
  """Install a command completer into readline.

  Args:
    completer: Either a CommandCompleter instance or a CommandRegistry.
  """
  if readline is None:
    logger.warning("readline_unavailable")
    return

  if isinstance(completer, CommandRegistry):
    completer = CommandCompleter(completer)

  try:
    readline.parse_and_bind("tab: complete")
    readline.set_completer(completer)
    logger.debug("command_completion_enabled")
  except Exception as exc:
    logger.warning("failed_to_setup_completion", error=str(exc))


def disable_completion() -> None:
  """Remove the active readline completer."""
  if readline is None:
    return

  try:
    readline.set_completer(None)
  except Exception as exc:
    logger.warning("failed_to_disable_completion", error=str(exc))


# Optional: factory for tests that don't want to touch the real readline module
def build_completer(
  registry: CommandRegistry,
  get_line_buffer: Callable[[], str] | None = None,
) -> CommandCompleter:
  """Build a CommandCompleter with an optional custom line-buffer function.

  The custom ``get_line_buffer`` is useful in tests to avoid depending on
  the global readline state.
  """
  return CommandCompleter(registry, get_line_buffer=get_line_buffer)
