"""Integration tests for the nonoka-cli server external capability flow.

These tests exercise ``ChatRequestHandler`` end-to-end with a mocked
``Orchestrator`` so no real LLM or model API key is required. They verify that
external MCP servers, external skills, and tool-call metadata are routed
through the bridge correctly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.handler import ChatRequestHandler
from nonoka_cli.bridge.protocol import (
  ChatMessage,
  ChatRequest,
  ExternalMCPServerDefinition,
  ExternalMCPToolDefinition,
  ExternalSkillDefinition,
  ExternalSkillToolDefinition,
  FinishEvent,
  TextDeltaEvent,
  ToolCall,
  ToolCallEvent,
)


def _make_request(
  messages: list[ChatMessage],
  external_mcp_servers: list[ExternalMCPServerDefinition] | None = None,
  external_skills: list[ExternalSkillDefinition] | None = None,
  tools: list[Any] | None = None,
) -> ChatRequest:
  return ChatRequest(
    messages=messages,
    external_mcp_servers=external_mcp_servers,
    external_skills=external_skills,
    tools=tools,
    cwd="/tmp/nonoka-integration",
    request_id="req-1",
  )


class _RecordingSender:
  def __init__(self) -> None:
    self.messages: list[Any] = []

  async def __call__(self, msg: Any) -> None:
    self.messages.append(msg)


def _fake_orchestrator() -> Any:
  """Return a minimal orchestrator-like object whose methods can be replaced."""
  return type("FakeOrchestrator", (), {"session_id": "sess-1"})()


def _handler_with_mock_orch() -> tuple[ChatRequestHandler, Any, _RecordingSender]:
  sender = _RecordingSender()
  handler = ChatRequestHandler(send=sender)
  handler._negotiate_protocol = AsyncMock(return_value=True)
  orch = _fake_orchestrator()
  handler._orchestrator = orch
  handler._session_id = "sess-1"
  handler._session_init_sent = True
  return handler, orch, sender


@pytest.mark.anyio
async def test_external_mcp_servers_and_skills_passed_to_execute():
  handler, orch, sender = _handler_with_mock_orch()
  captured: dict[str, Any] = {}

  async def _fake_execute(**kwargs: Any) -> AsyncIterator[StreamEvent]:
    captured["kwargs"] = kwargs
    yield StreamEvent(type="content_delta", data={"content": "ok"})
    yield StreamEvent(type="final", data={"success": True})

  orch.execute_with_external_tools = _fake_execute

  mcp_server = ExternalMCPServerDefinition(
    name="memory",
    description="Memory MCP server",
    tools=[
      ExternalMCPToolDefinition(
        name="read_memory",
        description="Read memory",
        parameters={"type": "object", "properties": {}},
      )
    ],
  )
  skill = ExternalSkillDefinition(
    name="todo",
    description="Todo skill",
    tools=[
      ExternalSkillToolDefinition(
        name="add_todo",
        description="Add a todo",
        parameters={"type": "object", "properties": {}},
      )
    ],
    system_prompt="You are a todo assistant.",
    activation_prompt="Activate todo skill.",
  )

  request = _make_request(
    messages=[ChatMessage(role="user", content="hello")],
    external_mcp_servers=[mcp_server],
    external_skills=[skill],
  )

  await handler.handle(request)

  assert captured["kwargs"]["external_mcp_servers"] == [mcp_server]
  assert captured["kwargs"]["external_skills"] == [skill]
  assert captured["kwargs"]["tools"] == []

  finish = sender.messages[-1]
  assert isinstance(finish, FinishEvent)
  assert finish.finish_reason == "stop"


@pytest.mark.anyio
async def test_tool_call_metadata_forwarded_to_outbound_event():
  handler, orch, sender = _handler_with_mock_orch()

  async def _fake_execute(**kwargs: Any) -> AsyncIterator[StreamEvent]:
    yield StreamEvent(
      type="tool_call_start",
      data={
        "tool_calls": [
          {
            "id": "tc-1",
            "function": {
              "name": "skill__todo__add_todo",
              "arguments": json.dumps({"title": "buy milk"}),
            },
            "metadata": {"kind": "skill", "skill": "todo"},
          }
        ]
      },
    )
    yield StreamEvent(type="final", data={"requires_external_execution": True})

  orch.execute_with_external_tools = _fake_execute

  request = _make_request(
    messages=[ChatMessage(role="user", content="add a todo")],
    external_skills=[
      ExternalSkillDefinition(
        name="todo",
        description="Todo skill",
        tools=[
          ExternalSkillToolDefinition(
            name="add_todo",
            description="Add a todo",
            parameters={"type": "object", "properties": {"title": {"type": "string"}}},
          )
        ],
      )
    ],
  )

  await handler.handle(request)

  tool_calls = [m for m in sender.messages if isinstance(m, ToolCallEvent)]
  assert len(tool_calls) == 1
  assert tool_calls[0].tool_name == "skill__todo__add_todo"
  assert tool_calls[0].metadata == {"kind": "skill", "skill": "todo"}

  finish = sender.messages[-1]
  assert isinstance(finish, FinishEvent)
  assert finish.finish_reason == "tool_calls"


@pytest.mark.anyio
async def test_resume_external_tools_passes_definitions():
  handler, orch, sender = _handler_with_mock_orch()
  captured: dict[str, Any] = {}

  async def _fake_resume(**kwargs: Any) -> AsyncIterator[StreamEvent]:
    captured["kwargs"] = kwargs
    yield StreamEvent(type="content_delta", data={"content": "resumed"})
    yield StreamEvent(type="final", data={"success": True})

  orch.resume_external_tools = _fake_resume

  mcp_server = ExternalMCPServerDefinition(
    name="memory",
    description="Memory MCP server",
    tools=[
      ExternalMCPToolDefinition(
        name="read_memory",
        description="Read memory",
        parameters={"type": "object", "properties": {}},
      )
    ],
  )
  skill = ExternalSkillDefinition(
    name="todo",
    description="Todo skill",
    tools=[
      ExternalSkillToolDefinition(
        name="add_todo",
        description="Add a todo",
        parameters={"type": "object", "properties": {}},
      )
    ],
  )

  request = _make_request(
    messages=[
      ChatMessage(role="system", content="You are helpful."),
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
          ToolCall(
            id="tc-1",
            name="skill__todo__add_todo",
            arguments=json.dumps({"title": "buy milk"}),
          )
        ],
      ),
      ChatMessage(
        role="tool",
        content="Todo added.",
        tool_call_id="tc-1",
      ),
    ],
    external_mcp_servers=[mcp_server],
    external_skills=[skill],
  )

  await handler.handle(request)

  assert captured["kwargs"]["external_mcp_servers"] == [mcp_server]
  assert captured["kwargs"]["external_skills"] == [skill]
  assert captured["kwargs"]["results"] == {"tc-1": "Todo added."}

  text_deltas = [m for m in sender.messages if isinstance(m, TextDeltaEvent)]
  assert len(text_deltas) == 1
  assert text_deltas[0].text == "resumed"
