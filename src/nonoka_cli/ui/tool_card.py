"""Tool call card renderer for streaming tool execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


@dataclass
class ToolCallState:
  """State of a single tool call during the stream."""

  tool_call_id: str
  name: str
  arguments: dict[str, Any] = field(default_factory=dict)
  status: str = "running"  # running | done | error
  result_preview: str = ""
  is_error: bool = False


class ToolCardRenderer:
  """Renders animated tool-call cards that update as results arrive.

  Maintains a list of tool calls keyed by ``tool_call_id`` so that
  ``tool_call_start`` creates a pending/running card and ``tool_call_result``
  updates it to done/error.
  """

  _SPINNER_NAME = "dots"

  def __init__(self, console: Console):
    self._console = console
    self._calls: dict[str, ToolCallState] = {}
    self._order: list[str] = []

  def reset(self) -> None:
    """Clear all tracked tool calls."""
    self._calls.clear()
    self._order.clear()

  def start(self, tool_calls: list[dict[str, Any]]) -> None:
    """Register new tool calls from a ``tool_call_start`` event."""
    for index, tc in enumerate(tool_calls):
      tc_id = tc.get("id") or tc.get("tool_call_id")
      if not tc_id:
        # Generate a deterministic fallback id so multiple tools without ids
        # do not collapse into a single entry.
        tc_id = f"__auto_{index}_{len(self._calls)}__"
      func = tc.get("function", {})
      name = func.get("name") or tc.get("name", "unknown")
      arguments = func.get("arguments", tc.get("arguments", {}))
      if isinstance(arguments, str):
        try:
          arguments = json.loads(arguments)
        except json.JSONDecodeError:
          arguments = {"raw": arguments}

      state = ToolCallState(
        tool_call_id=tc_id,
        name=name,
        arguments=arguments or {},
        status="running",
      )
      if tc_id not in self._calls:
        self._order.append(tc_id)
      self._calls[tc_id] = state

  def finish(
    self,
    tool_call_id: str,
    name: str,
    result_preview: str,
    is_error: bool,
  ) -> None:
    """Update a tool call with its result."""
    tc_id = tool_call_id or "unknown"
    state = self._calls.get(tc_id)
    if state is None:
      # Result arrived without a matching start — create a transient entry.
      state = ToolCallState(
        tool_call_id=tc_id,
        name=name or "unknown",
        status="error" if is_error else "done",
        result_preview=result_preview,
        is_error=is_error,
      )
      self._calls[tc_id] = state
      self._order.append(tc_id)
    else:
      state.status = "error" if is_error else "done"
      state.result_preview = result_preview
      state.is_error = is_error

  @property
  def has_tools(self) -> bool:
    """Whether any tool calls are being tracked."""
    return bool(self._calls)

  def __rich__(self) -> RenderableType:
    """Render all tool cards as a vertical stack of panels."""
    if not self._calls:
      return Text("")

    cards: list[Panel] = []
    for tc_id in self._order:
      state = self._calls[tc_id]
      cards.append(self._render_card(state))

    from rich.console import Group

    return Group(*cards)

  def _render_card(self, state: ToolCallState) -> Panel:
    """Render a single tool call card."""
    if state.status == "running":
      title = f"[bold yellow]⚙ Running[/bold yellow] [cyan]{state.name}[/cyan]"
      border_style = "yellow"
    elif state.status == "error":
      title = f"[bold red]✗ Error[/bold red] [cyan]{state.name}[/cyan]"
      border_style = "red"
    else:
      title = f"[bold green]✓ Done[/bold green] [cyan]{state.name}[/cyan]"
      border_style = "green"

    table = Table(show_header=False, box=None, padding=0)
    table.add_column("key", style="dim", no_wrap=True)
    table.add_column("value", style="white", ratio=1)

    args_text = self._format_arguments(state.arguments)
    table.add_row("Arguments:", args_text)

    if state.status != "running":
      result = state.result_preview or "(no output)"
      if len(result) > 500:
        result = result[:500] + "\n[dim]... truncated[/dim]"
      style = "red" if state.is_error else "green"
      table.add_row(
        "Result:",
        Text(result, style=style),
      )
    else:
      table.add_row("Status:", Spinner(self._SPINNER_NAME, text="Running..."))

    return Panel(
      table,
      title=title,
      border_style=border_style,
      padding=(0, 1),
    )

  @staticmethod
  def _format_arguments(arguments: Any) -> str:
    """Format tool arguments for display."""
    if not arguments:
      return "[dim]none[/dim]"
    try:
      return json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
    except Exception:
      return str(arguments)
