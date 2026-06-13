"""Stream renderer for nonoka-cli.

Coordinates the sub-renderers (content, tool cards, errors, stats) and
streams nonoka ``StreamEvent`` objects to the terminal via rich.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel

from nonoka.core.runner import StreamEvent

from nonoka_cli.ui.console import get_console
from nonoka_cli.ui.content import ContentRenderer
from nonoka_cli.ui.error import ErrorRenderer
from nonoka_cli.ui.stats import StatsRenderer
from nonoka_cli.ui.tool_card import ToolCardRenderer


class Renderer:
  """Renders nonoka ``StreamEvent`` stream to the terminal.

  Uses a ``rich.Live`` display to update content, tool cards, errors and
  stats in place. For non-interactive / test environments the live display
  can be disabled with ``use_live=False``.
  """

  def __init__(
    self,
    console: Console | None = None,
    *,
    use_live: bool = True,
    code_theme: str = "monokai",
  ):
    """Args:
      console: rich Console to render to. Defaults to the global console.
      use_live: Whether to use ``rich.Live`` for in-place updates. Disable
        for tests or when writing to a plain file.
      code_theme: Pygments theme for fenced code blocks.
    """
    self._console = console or get_console()
    self._use_live = use_live
    self._content = ContentRenderer(self._console, code_theme=code_theme)
    self._tool_card = ToolCardRenderer(self._console)
    self._error = ErrorRenderer(self._console)
    self._stats = StatsRenderer(self._console)

    self._live: Live | None = None
    self._start_time: float | None = None
    self._duration: float = 0.0
    self._turn_count: int = 0
    self._tool_call_count: int = 0
    self._last_event_type: str | None = None
    self._error_message: str | None = None
    self._error_type: str | None = None
    self._show_stats: bool = False

  async def render_stream(self, stream: AsyncIterator[StreamEvent]) -> None:
    """Consume a ``StreamEvent`` async iterator and render to terminal."""
    self._reset_state()
    self._start_time = time.monotonic()

    if self._use_live:
      with Live(
        self._build_layout(),
        console=self._console,
        auto_refresh=False,
        transient=False,
      ) as live:
        self._live = live
        async for event in stream:
          self._update_state(event)
          live.update(self._build_layout())
          live.refresh()
      self._live = None
    else:
      async for event in stream:
        self._update_state(event)
        self._console.print(self._build_layout())

  def _reset_state(self) -> None:
    """Reset all per-stream state."""
    self._content.reset()
    self._tool_card.reset()
    self._start_time = None
    self._duration = 0.0
    self._turn_count = 0
    self._tool_call_count = 0
    self._last_event_type = None
    self._error_message = None
    self._error_type = None
    self._show_stats = False

  def _update_state(self, event: StreamEvent) -> None:
    """Apply a single stream event to internal state."""
    event_type = event.type

    if event_type in ("content_delta", "tool_call_start"):
      # A new LLM response begins after tool results, an error, or at startup.
      if self._last_event_type in (None, "tool_call_result", "error", "final"):
        self._turn_count += 1

    match event_type:
      case "content_delta":
        content = event.data.get("content", "")
        if content:
          self._content.append(content)

      case "tool_call_start":
        tool_calls = event.data.get("tool_calls", [])
        self._tool_call_count += len(tool_calls)
        self._tool_card.start(tool_calls)

      case "tool_call_result":
        self._tool_card.finish(
          event.data.get("tool_call_id", "unknown"),
          event.data.get("name", ""),
          event.data.get("result_preview", ""),
          event.data.get("is_error", False),
        )

      case "error":
        self._error_message = event.data.get("error", "Unknown error")
        self._error_type = event.data.get("error_type", "error")

      case "final":
        self._content.finalize()
        if self._start_time is not None:
          self._duration = time.monotonic() - self._start_time

        # Prefer exact stats emitted by nonoka (>=1.1.8); fall back to local estimates.
        event_turn_count = event.data.get("turn_count")
        if isinstance(event_turn_count, int) and event_turn_count > 0:
          self._turn_count = event_turn_count
        elif self._turn_count == 0:
          self._turn_count = 1

        event_tool_count = event.data.get("tool_call_count")
        if isinstance(event_tool_count, int):
          self._tool_call_count = event_tool_count

        event_duration = event.data.get("duration_seconds")
        if isinstance(event_duration, (int, float)) and event_duration >= 0:
          self._duration = float(event_duration)

        self._show_stats = True

    self._last_event_type = event_type

  def _build_layout(self) -> RenderableType:
    """Build the current renderable layout."""
    parts: list[RenderableType] = []

    content_renderable = self._content.__rich__()
    if self._content.text or self._content.is_finalized:
      parts.append(Panel(content_renderable, border_style="blue", padding=(0, 1)))

    if self._tool_card.has_tools:
      parts.append(self._tool_card.__rich__())

    if self._error_message is not None:
      parts.append(self._error.render(self._error_message, self._error_type))

    if self._show_stats:
      parts.append(
        self._stats.render(
          duration=self._duration,
          turns=self._turn_count,
          tool_calls=self._tool_call_count,
        )
      )

    if not parts:
      return ""

    if len(parts) == 1:
      return parts[0]

    return Group(*parts)

  def clear_current_output(self) -> None:
    """Clear the current live output area.

    Stops the live renderer if it is running and resets accumulated state.
    """
    if self._live is not None:
      self._live.stop()
      self._live = None
    self._reset_state()
