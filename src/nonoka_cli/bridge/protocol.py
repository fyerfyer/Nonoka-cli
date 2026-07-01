"""NDJSON message protocol for the nonoka-cli server mode.

All messages on stdin/stdout are single-line JSON objects separated by ``\n``.
This protocol is intentionally minimal for Phase 1 and targets the
Vercel AI SDK custom provider bridge.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Inbound messages: OpenCode provider -> nonoka-cli
# --------------------------------------------------------------------------- #


class ChatMessage(BaseModel):
  """A single message in the chat history."""

  role: Literal["system", "user", "assistant", "tool"]
  content: str
  tool_call_id: str | None = None


class ChatRequest(BaseModel):
  """Request nonoka-cli to run one user turn."""

  type: Literal["chat"] = "chat"
  messages: list[ChatMessage]
  session_id: str | None = None
  new_session: bool = False
  cwd: str = Field(default=".")
  model: str | None = None


class ApprovalResponse(BaseModel):
  """User approval decision for a pending tool operation."""

  type: Literal["approval"] = "approval"
  id: str
  approved: bool
  modified_args: dict[str, Any] | None = None


InboundMessage = ChatRequest | ApprovalResponse


# --------------------------------------------------------------------------- #
# Outbound messages: nonoka-cli -> OpenCode provider
# --------------------------------------------------------------------------- #


class SessionInitEvent(BaseModel):
  """Emitted on the first response so the provider can persist the session id."""

  type: Literal["session_init"] = "session_init"
  session_id: str


class TextDeltaEvent(BaseModel):
  """Incremental assistant text."""

  type: Literal["text_delta"] = "text_delta"
  text: str


class FinishEvent(BaseModel):
  """A single assistant turn finished."""

  type: Literal["finish"] = "finish"
  finish_reason: Literal["stop", "error", "cancel", "approval_required", "tool_calls"]


class ToolCallEvent(BaseModel):
  """A tool call was requested by the agent (observation only)."""

  type: Literal["tool_call"] = "tool_call"
  tool_call_id: str
  tool_name: str
  args: Any = None


class ToolResultEvent(BaseModel):
  """A tool call finished (observation only)."""

  type: Literal["tool_result"] = "tool_result"
  tool_call_id: str
  tool_name: str
  content: str
  result: Any = None
  is_error: bool = False


class ApprovalRequestEvent(BaseModel):
  """A tool operation requires human approval."""

  type: Literal["approval_request"] = "approval_request"
  id: str
  tool_call_id: str
  tool_name: str
  args: Any = None


class ErrorEvent(BaseModel):
  """Fatal or terminal error."""

  type: Literal["error"] = "error"
  message: str


OutboundMessage = (
  SessionInitEvent
  | TextDeltaEvent
  | ToolCallEvent
  | ToolResultEvent
  | ApprovalRequestEvent
  | FinishEvent
  | ErrorEvent
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def parse_inbound_line(line: str) -> InboundMessage | None:
  """Parse a single inbound NDJSON line into a typed message.

  Args:
    line: Raw JSON line from stdin.

  Returns:
    A typed message, or None if the line is empty/whitespace.

  Raises:
    ValueError: If the JSON is invalid or the type is unknown.
  """
  line = line.strip()
  if not line:
    return None

  data: dict[str, Any] = __import__("json").loads(line)
  msg_type = data.get("type")

  if msg_type == "chat":
    return ChatRequest.model_validate(data)

  if msg_type == "approval":
    return ApprovalResponse.model_validate(data)

  raise ValueError(f"Unknown inbound message type: {msg_type}")


def encode_outbound_message(msg: OutboundMessage) -> str:
  """Serialize an outbound message to a single NDJSON line."""
  return msg.model_dump_json(exclude_none=True)
