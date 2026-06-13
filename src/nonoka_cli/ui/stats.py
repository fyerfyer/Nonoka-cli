"""Execution statistics renderer for nonoka-cli."""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table


class StatsRenderer:
  """Renders a compact execution statistics panel."""

  def __init__(self, console: Console):
    self._console = console

  def render(
    self,
    duration: float,
    turns: int,
    tool_calls: int,
  ) -> RenderableType:
    """Render the stats panel.

    Args:
      duration: Total execution time in seconds.
      turns: Number of ReAct turns (LLM calls).
      tool_calls: Number of tool calls executed.

    Returns:
      A rich renderable ready for console output.
    """
    table = Table(show_header=False, box=None, padding=0)
    table.add_column("metric", style="dim", no_wrap=True)
    table.add_column("value", style="cyan", no_wrap=True)

    table.add_row("Duration:", f"{duration:.2f}s")
    table.add_row("Turns:", str(turns))
    table.add_row("Tool calls:", str(tool_calls))

    return Panel(
      table,
      title="[bold dim]Run Stats[/bold dim]",
      border_style="dim",
      padding=(0, 1),
    )
