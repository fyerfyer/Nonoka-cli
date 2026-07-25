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


class ToolCall(BaseModel):
  """A tool call attached to an assistant message."""

  id: str
  name: str
  arguments: str


class ChatMessage(BaseModel):
  """A single message in the chat history."""

  role: Literal["system", "user", "assistant", "tool"]
  content: str
  tool_call_id: str | None = None
  tool_calls: list[ToolCall] | None = None
  result: Any = None


class ExternalToolDefinition(BaseModel):
  """A tool definition supplied by an external host (e.g. OpenCode)."""

  name: str
  description: str
  parameters: dict[str, Any]


class ExternalMCPToolDefinition(BaseModel):
  """A tool provided by an external host-managed MCP server."""

  name: str
  description: str
  parameters: dict[str, Any]


class ExternalMCPServerDefinition(BaseModel):
  """An external host-managed MCP server definition."""

  name: str
  description: str = ""
  tools: list[ExternalMCPToolDefinition]


class ExternalSkillToolDefinition(BaseModel):
  """A tool provided by an external host-managed skill."""

  name: str
  description: str
  parameters: dict[str, Any]


class ExternalSkillDefinition(BaseModel):
  """An external host-managed skill definition."""

  name: str
  description: str = ""
  tools: list[ExternalSkillToolDefinition]
  system_prompt: str = ""
  activation_prompt: str = ""


class ChatRequest(BaseModel):
  """Request nonoka-cli to run one user turn."""

  type: Literal["chat"] = "chat"
  purpose: Literal["chat", "title"] = "chat"
  messages: list[ChatMessage]
  tools: list[ExternalToolDefinition] | None = None
  external_mcp_servers: list[ExternalMCPServerDefinition] | None = None
  external_skills: list[ExternalSkillDefinition] | None = None
  session_id: str | None = None
  new_session: bool = False
  cwd: str = Field(default=".")
  model: str | None = None
  temperature: float | None = None
  max_turns: int | None = Field(default=None, ge=1)
  timeout_seconds: float | None = Field(default=None, gt=0)
  wall_timeout_seconds: float | None = Field(default=None, gt=0)
  tool_budget: int | None = Field(default=None, ge=1)
  max_context_bytes: int | None = Field(default=None, ge=1)
  max_external_result_bytes: int | None = Field(default=None, ge=1)
  require_workspace_mutation: bool = False
  require_observed_effect: bool = False
  request_id: str | None = None


class CancelRequest(BaseModel):
  """Request cancellation of the bridge's currently active chat turn."""

  type: Literal["cancel"] = "cancel"
  request_id: str | None = None


InboundMessage = ChatRequest | CancelRequest


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
  termination: dict[str, Any] | None = None
  runtime: dict[str, Any] | None = None


class ToolCallEvent(BaseModel):
  """A tool call was requested by the agent (observation only)."""

  type: Literal["tool_call"] = "tool_call"
  tool_call_id: str
  tool_name: str
  args: Any = None
  metadata: dict[str, Any] | None = None


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


class DebugEvent(BaseModel):
  """Structured debug information for adapter/frontend logs.

  OpenCode does not render this event; it is emitted only when
  ``NONOKA_DEBUG=1`` so that developers can inspect message order, tool
  lists, and session state without polluting the TUI.
  """

  type: Literal["debug"] = "debug"
  level: Literal["info", "warning", "error"] = "info"
  message: str
  payload: dict[str, Any] | None = None


class ErrorEvent(BaseModel):
  """Fatal or terminal error."""

  type: Literal["error"] = "error"
  message: str
  code: str | None = None
  retryable: bool | None = None
  details: dict[str, Any] | None = None


OutboundMessage = (
  SessionInitEvent
  | TextDeltaEvent
  | ToolCallEvent
  | ToolResultEvent
  | ApprovalRequestEvent
  | FinishEvent
  | DebugEvent
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
  if msg_type == "cancel":
    return CancelRequest.model_validate(data)

  raise ValueError(f"Unknown inbound message type: {msg_type}")


def encode_outbound_message(msg: OutboundMessage) -> str:
  """Serialize an outbound message to a single NDJSON line."""
  return msg.model_dump_json(exclude_none=True)
