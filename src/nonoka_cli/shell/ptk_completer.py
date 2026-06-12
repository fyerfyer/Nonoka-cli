"""prompt-toolkit completer for CLI slash commands.

Adapts the CommandRegistry to prompt-toolkit's Completer protocol so that
Tab / arrow-key selection works in the bottom input area.
"""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion

from nonoka_cli.shell.commands import CommandRegistry


class PTCommandCompleter(Completer):
  """Completer that exposes all registered /-commands.

  Only activates when the input starts with ``/``. Typing ``/`` followed by
  Tab (or just ``/`` when the completion menu is auto-triggered) shows the
  list of available slash commands, which the user can navigate with the
  arrow keys.
  """

  def __init__(self, registry: CommandRegistry) -> None:
    """Args:
      registry: Command registry containing available slash commands.
    """
    self._registry = registry

  def get_completions(self, document, complete_event) -> None:
    """Yield Completion objects for matching slash commands."""
    text = document.text
    if not text.startswith("/"):
      return

    prefix = text[1:]
    for name in self._registry.names():
      if name.startswith(prefix):
        yield Completion(
          text=f"/{name}",
          start_position=-len(text),
          display=f"/{name}",
        )
