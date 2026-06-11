"""Basic text renderer — streams nonoka StreamEvent to the terminal.

TODO: Add Markdown rendering, code highlighting, tool cards, stats panel.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

from nonoka.core.runner import StreamEvent


class Renderer:
  """Renders nonoka StreamEvent stream to the terminal.

  Implements minimal text streaming:
  - content_delta: print incremental text
  - tool_call_start / tool_call_result: print simple indicators
  - error: print error message
  - final: newline after response
  """

  def __init__(self, file: object | None = None):
    """Args:
      file: Output stream (default: sys.stdout).
    """
    self._file = file or sys.stdout
    self._in_tool_call = False

  async def render_stream(self, stream: AsyncIterator[StreamEvent]) -> None:
    """Consume a StreamEvent async iterator and render to terminal.

    Args:
      stream: AsyncIterator of StreamEvent from nonoka Runner.
    """
    async for event in stream:
      self._render_event(event)

  def _render_event(self, event: StreamEvent) -> None:
    """Render a single StreamEvent."""
    match event.type:
      case "content_delta":
        content = event.data.get("content", "")
        if content:
          self._file.write(content)
          self._file.flush()

      case "tool_call_start":
        self._in_tool_call = True
        tool_calls = event.data.get("tool_calls", [])
        names = [tc.get("name", "unknown") for tc in tool_calls]
        self._file.write(f"\n[Tool: {', '.join(names)}]\n")
        self._file.flush()

      case "tool_call_result":
        self._in_tool_call = False
        result = event.data.get("result")
        if isinstance(result, dict) and "error" in result:
          self._file.write(f"[Tool error: {result['error']}]\n")
        else:
          self._file.write("[Tool done]\n")
        self._file.flush()

      case "error":
        error_msg = event.data.get("error", "Unknown error")
        error_type = event.data.get("error_type", "error")
        self._file.write(f"\n[{error_type.upper()}] {error_msg}\n")
        self._file.flush()

      case "final":
        if not self._in_tool_call:
          self._file.write("\n")
          self._file.flush()

  def clear_current_output(self) -> None:
    """Clear current output area (no-op for basic renderer)."""
    pass
