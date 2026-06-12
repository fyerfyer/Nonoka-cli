"""prompt-toolkit based bottom input area for nonoka-cli.

Provides a dedicated input line at the bottom of the terminal. stdout/stderr
output from the rest of the application is automatically redirected above the
input area via ``patch_stdout``.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.input import Input
from prompt_toolkit.output import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts.prompt import CompleteStyle

from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.shell.ptk_completer import PTCommandCompleter


_PROMPT_MESSAGE = HTML(
  "<b><style fg=\"ansigreen\">nonoka</style></b>"
  "<style fg=\"ansiwhite\">></style> "
)


class PromptInput:
  """Bottom input area for the REPL.

  Uses prompt-toolkit's ``PromptSession`` to render a styled prompt at the
  bottom of the terminal. All other stdout writes are patched to appear above
  the input line. The completer exposes registered slash commands and supports
  arrow-key navigation through the completion menu.
  """

  def __init__(
    self,
    registry: CommandRegistry,
    input: Input | None = None,
    output: Output | None = None,
  ):
    """Initialize the input area.

    Args:
      registry: Command registry used for slash-command completion.
      input: Optional prompt-toolkit Input (for testing).
      output: Optional prompt-toolkit Output (for testing).
    """
    self._session = PromptSession(
      message=_PROMPT_MESSAGE,
      completer=PTCommandCompleter(registry),
      complete_style=CompleteStyle.MULTI_COLUMN,
      complete_while_typing=True,
      input=input,
      output=output,
    )

  async def read(self) -> str:
    """Read a line of input from the bottom input area.

    Returns:
      Stripped user input string.

    Raises:
      EOFError: On Ctrl+D.
      KeyboardInterrupt: On Ctrl+C.
    """
    with patch_stdout():
      text = await self._session.prompt_async()
    return text.strip()
