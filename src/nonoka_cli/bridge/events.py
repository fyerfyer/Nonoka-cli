"""Translate nonoka StreamEvents into bridge outbound NDJSON messages."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
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

_TIMELINE_PATH = Path(
  os.environ.get("NONOKA_TIMELINE_PATH") or "/tmp/nonoka-tui-timeline.ndjson"
)


def _timeline_log(event: StreamEvent, messages: list[OutboundMessage]) -> None:
  """Append a structured event to the shared TUI timeline log."""
  try:
    with open(_TIMELINE_PATH, "a", encoding="utf-8") as f:
      for msg in messages:
        record: dict[str, Any] = {
          "ts": datetime.now(timezone.utc).isoformat(),
          "source": "bridge",
          "type": msg.type,
        }
        if msg.type == "text_delta":
          record["len"] = len(msg.text)
          record["has_newline"] = "\n" in msg.text
          record["preview"] = msg.text[:80].replace("\n", "\\n")
        elif msg.type in {"tool_call", "tool_result"}:
          record["toolName"] = getattr(msg, "tool_name", "")
          record["toolCallId"] = getattr(msg, "tool_call_id", "")
        elif msg.type == "approval_request":
          record["toolName"] = getattr(msg, "tool_name", "")
          record["toolCallId"] = getattr(msg, "tool_call_id", "")
        elif msg.type == "finish":
          record["finish_reason"] = msg.finish_reason
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
  except Exception:
    # Timeline logging is best-effort; never fail the stream because of it.
    pass


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
        messages = [TextDeltaEvent(text=content)]
        _timeline_log(event, messages)
        return messages
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
      _timeline_log(event, out)
      return out

    case "tool_call_result":
      messages = [
        ToolResultEvent(
          tool_call_id=event.data.get("tool_call_id", "unknown"),
          tool_name=event.data.get("name", ""),
          content=event.data.get("result_preview", ""),
          result=event.data.get("result"),
          is_error=bool(event.data.get("is_error", False)),
        )
      ]
      _timeline_log(event, messages)
      return messages

    case "approval_request":
      messages = [
        ApprovalRequestEvent(
          id=event.data.get("tool_call_id", "unknown"),
          tool_call_id=event.data.get("tool_call_id", "unknown"),
          tool_name=event.data.get("tool_name", ""),
          args=event.data.get("args"),
        )
      ]
      _timeline_log(event, messages)
      return messages

    case "error":
      messages = [
        ErrorEvent(message=event.data.get("error", "Unknown error")),
        FinishEvent(finish_reason="error"),
      ]
      _timeline_log(event, messages)
      return messages

    case "final":
      if event.data.get("requires_approval"):
        messages = [FinishEvent(finish_reason="approval_required")]
      elif event.data.get("requires_external_execution"):
        messages = [FinishEvent(finish_reason="tool_calls")]
      else:
        finish_reason = "stop" if event.data.get("success", False) else "error"
        messages = [FinishEvent(finish_reason=finish_reason)]
      _timeline_log(event, messages)
      return messages

    case _:
      return []
