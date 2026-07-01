"""Translate nonoka StreamEvents into bridge outbound NDJSON messages."""

from __future__ import annotations

import json
from typing import Any

from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.protocol import (
  ApprovalRequestEvent,
  ErrorEvent,
  FinishEvent,
  OutboundMessage,
  TextDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
)


def _stringify_args(args: Any) -> str:
  """Return a JSON string for tool arguments, falling back to str()."""
  if isinstance(args, str):
    return args
  try:
    return json.dumps(args, ensure_ascii=False, default=str)
  except (TypeError, ValueError):
    return str(args)


def translate_stream_event(event: StreamEvent) -> list[OutboundMessage]:
  """Convert a single nonoka StreamEvent to zero or more outbound messages."""
  match event.type:
    case "content_delta":
      content = event.data.get("content", "")
      if content:
        return [TextDeltaEvent(text=content)]
      return []

    case "tool_call_start":
      out: list[OutboundMessage] = []
      for tc in event.data.get("tool_calls") or []:
        func = tc.get("function", {})
        name = func.get("name", "")
        args = func.get("arguments", "{}")
        if isinstance(args, str):
          try:
            args = json.loads(args) if args else {}
          except json.JSONDecodeError:
            args = {"raw": args}
        out.append(
          ToolCallEvent(
            tool_call_id=tc.get("id") or tc.get("tool_call_id", "unknown"),
            tool_name=name,
            args=args,
          )
        )
      return out

    case "tool_call_result":
      return [
        ToolResultEvent(
          tool_call_id=event.data.get("tool_call_id", "unknown"),
          tool_name=event.data.get("name", ""),
          content=event.data.get("result_preview", ""),
          result=event.data.get("result"),
          is_error=bool(event.data.get("is_error", False)),
        )
      ]

    case "approval_request":
      return [
        ApprovalRequestEvent(
          id=event.data.get("tool_call_id", "unknown"),
          tool_call_id=event.data.get("tool_call_id", "unknown"),
          tool_name=event.data.get("tool_name", ""),
          args=event.data.get("args"),
        )
      ]

    case "error":
      return [
        ErrorEvent(message=event.data.get("error", "Unknown error")),
        FinishEvent(finish_reason="error"),
      ]

    case "final":
      if event.data.get("requires_approval"):
        return [FinishEvent(finish_reason="approval_required")]
      finish_reason = "stop" if event.data.get("success", False) else "error"
      return [FinishEvent(finish_reason=finish_reason)]

    case _:
      return []
