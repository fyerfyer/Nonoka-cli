"""prompt-toolkit based bottom input area for nonoka-cli.

Provides a dedicated input line at the bottom of the terminal. stdout/stderr
output from the rest of the application is automatically redirected above the
input area via ``patch_stdout``.

Supports:
- Slash-command tab completion
- Persistent input history saved to ``~/.local/share/nonoka/history``
- Multi-line input triggered by ``\"\"\"`` or unclosed brackets/quotes
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import History
from prompt_toolkit.input import Input
from prompt_toolkit.output import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts.prompt import CompleteStyle

from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.shell.history import TrimmingFileHistory
from nonoka_cli.shell.ptk_completer import PTCommandCompleter


_PROMPT_MESSAGE = HTML(
  "<b><style fg=\"ansigreen\">nonoka</style></b>"
  "<style fg=\"ansiwhite\">></style> "
)

_CONTINUATION_MESSAGE = HTML(
  "<style fg=\"ansigreen\">...</style> "
)

_DEFAULT_HISTORY_PATH = Path.home() / ".local" / "share" / "nonoka" / "history"


class PromptInput:
  """Bottom input area for the REPL.

  Uses prompt-toolkit's ``PromptSession`` to render a styled prompt at the
  bottom of the terminal. All other stdout writes are patched to appear above
  the input line. The completer exposes registered slash commands and supports
  arrow-key navigation through the completion menu.

  Multi-line input is detected automatically when the user enters a line that
  starts or contains an unclosed ``\"\"\"`` block, or that has unbalanced
  brackets, parentheses, or quotes.
  """

  def __init__(
    self,
    registry: CommandRegistry,
    *,
    input: Input | None = None,
    output: Output | None = None,
    history: History | None = None,
    history_path: Path | str | None = None,
    max_history: int = 1000,
    multi_line_trigger: str = '"""',
  ):
    """Initialize the input area.

    Args:
      registry: Command registry used for slash-command completion.
      input: Optional prompt-toolkit Input (for testing).
      output: Optional prompt-toolkit Output (for testing).
      history: Optional history instance. If provided, ``history_path`` and
        ``max_history`` are ignored. Useful for tests.
      history_path: Path to the persistent history file. Defaults to
        ``~/.local/share/nonoka/history``.
      max_history: Maximum number of history entries to retain on disk.
      multi_line_trigger: String that triggers multi-line mode when it appears
        unclosed in the input.
    """
    self._registry = registry
    self._multi_line_trigger = multi_line_trigger
    self._history = history
    if self._history is None:
      path = Path(history_path) if history_path is not None else _DEFAULT_HISTORY_PATH
      path.parent.mkdir(parents=True, exist_ok=True)
      self._history = TrimmingFileHistory(str(path), max_entries=max_history)

    self._session = PromptSession(
      message=_PROMPT_MESSAGE,
      completer=PTCommandCompleter(registry),
      complete_style=CompleteStyle.MULTI_COLUMN,
      complete_while_typing=True,
      history=self._history,
      input=input,
      output=output,
      multiline=False,
    )

  async def read(self) -> str:
    """Read a line (or multi-line block) of input from the bottom input area.

    Returns:
      Stripped user input string. For multi-line input, lines are joined with
      newline characters.

    Raises:
      EOFError: On Ctrl+D.
      KeyboardInterrupt: On Ctrl+C.
    """
    with patch_stdout():
      first_line = await self._session.prompt_async()
      text = first_line.rstrip('\n')

      # Collect continuation lines until the input is complete.
      while text and not self._is_complete(text):
        continuation = await self._session.prompt_async(
          message=_CONTINUATION_MESSAGE,
          completer=self._session.completer,
          complete_style=self._session.complete_style,
          complete_while_typing=self._session.complete_while_typing,
        )
        text = f"{text}\n{continuation.rstrip('\n')}"

    return text.strip()

  def _is_complete(self, text: str) -> bool:
    """Return True if the accumulated text forms a complete input.

    Incomplete cases:
    - The multi-line trigger (``\"\"\"``) is opened but not closed.
    - Brackets, parentheses, braces, or quotes are unbalanced.
    """
    # Multi-line trigger detection.
    trigger = self._multi_line_trigger
    if trigger:
      count = text.count(trigger)
      if count > 0 and count % 2 != 0:
        return False

    # Balance detection for quotes and brackets.
    stack: list[str] = []
    in_single_quote = False
    in_double_quote = False
    in_triple_quote = False
    i = 0
    chars = list(text)
    while i < len(chars):
      ch = chars[i]
      next_ch = chars[i + 1] if i + 1 < len(chars) else ""
      next_next_ch = chars[i + 2] if i + 2 < len(chars) else ""

      if not in_single_quote and not in_double_quote:
        if ch == '"' and next_ch == '"' and next_next_ch == '"':
          in_triple_quote = not in_triple_quote
          i += 3
          continue

      if not in_triple_quote:
        if ch == "'" and not in_double_quote:
          in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
          in_double_quote = not in_double_quote
        elif not in_single_quote and not in_double_quote:
          if ch in "([{":
            stack.append(ch)
          elif ch in ")]}:":
            if not stack:
              return True  # Mismatched closing bracket; treat as complete.
            opening = stack.pop()
            if (opening == "(" and ch != ")") or \
               (opening == "[" and ch != "]") or \
               (opening == "{" and ch != "}"):
              return True  # Mismatched pair; treat as complete.
      i += 1

    return not stack and not in_single_quote and not in_double_quote and not in_triple_quote
