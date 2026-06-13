"""Incremental content renderer for streaming LLM output.

Accumulates raw Markdown text during the stream and finalizes it into a
syntax-highlighted Markdown render once the response is complete.
"""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.text import Text


class ContentRenderer:
  """Renders incremental LLM text, finalizing as Markdown.

  While streaming, the raw buffer is displayed so tokens appear immediately.
  When ``finalize()`` is called (typically on the ``final`` event), the buffer
  is converted to a ``rich.Markdown`` object with code-block syntax
  highlighting.

  Args:
    console: The rich Console used to resolve the active theme.
    code_theme: Pygments theme for fenced code blocks. Defaults to a
      theme that works well on dark terminals; set to ``"default"`` for
      light themes.
  """

  def __init__(self, console: Console, code_theme: str = "monokai"):
    self._console = console
    self._code_theme = code_theme
    self._buffer = ""
    self._finalized = False

  def reset(self) -> None:
    """Clear the buffer and return to streaming mode."""
    self._buffer = ""
    self._finalized = False

  def append(self, text: str) -> None:
    """Append incremental text to the buffer."""
    self._buffer += text

  def finalize(self) -> None:
    """Mark the response as complete so Markdown rendering is used."""
    self._finalized = True

  @property
  def is_finalized(self) -> bool:
    """Whether the content has been finalized."""
    return self._finalized

  @property
  def text(self) -> str:
    """The accumulated raw text buffer."""
    return self._buffer

  def __rich__(self) -> RenderableType:
    """Render the current content state.

    Returns a ``Markdown`` object when finalized, otherwise the raw text.
    """
    if self._finalized and self._buffer:
      return Markdown(self._buffer, code_theme=self._code_theme)
    return Text(self._buffer, no_wrap=False)
