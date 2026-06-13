"""Error rendering for nonoka-cli."""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.text import Text


class ErrorRenderer:
  """Renders runtime errors as styled red blocks."""

  def __init__(self, console: Console):
    self._console = console

  def render(self, message: str, error_type: str | None = None) -> RenderableType:
    """Render an error message in a red panel.

    Args:
      message: Human-readable error message.
      error_type: Optional error category (e.g. ``llm_error``,
        ``tool_error``). Used as the panel title.

    Returns:
      A rich renderable ready for console output.
    """
    title = error_type or "error"
    title = title.replace("_", " ").title()

    return Panel(
      Text(message, style="red"),
      title=f"[bold red]{title}[/bold red]",
      border_style="red",
      padding=(0, 1),
    )
