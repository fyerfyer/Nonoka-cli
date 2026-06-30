"""Translate nonoka StreamEvents into bridge outbound NDJSON messages."""

from __future__ import annotations

from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.protocol import (
  ErrorEvent,
  FinishEvent,
  OutboundMessage,
  TextDeltaEvent,
)


def translate_stream_event(event: StreamEvent) -> list[OutboundMessage]:
  """Convert a single nonoka StreamEvent to zero or more outbound messages.

  The bridge protocol intentionally emits assistant text, errors, and finish
  markers. Tool call observations are reserved for future phases where the
  provider can surface them to OpenCode without triggering duplicate execution.
  """
  match event.type:
    case "content_delta":
      content = event.data.get("content", "")
      if content:
        return [TextDeltaEvent(text=content)]
      return []

    case "tool_call_start":
      # Phase 1 does not stream tool calls; they are handled internally by
      # nonoka. Future phases can emit ToolCallEvent here for observation.
      return []

    case "tool_call_result":
      # Phase 1 does not stream tool results.
      return []

    case "error":
      return [
        ErrorEvent(message=event.data.get("error", "Unknown error")),
        FinishEvent(finish_reason="error"),
      ]

    case "final":
      finish_reason = "stop" if event.data.get("success", False) else "error"
      return [FinishEvent(finish_reason=finish_reason)]

    case _:
      return []
