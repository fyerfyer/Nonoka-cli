"""Tests for ChatRequestHandler."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.bridge.handler import ChatRequestHandler
from nonoka_cli.bridge.protocol import ChatMessage, ChatRequest, ExternalToolDefinition, ToolCall


@pytest.fixture
def sent():
  return []


@pytest.fixture
def handler(sent):
  return ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))


async def test_handle_new_session(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "new-session-id"
      orc.new_session = AsyncMock(return_value="new-session-id")
      orc.execute = MagicMock(return_value=async_empty())
      handler._orchestrator = orc

      msg = ChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        new_session=True,
      )
      await handler.handle(msg)

      orc.new_session.assert_awaited_once()
      orc.execute.assert_called_once_with("hello", working_dir=handler._working_dir)
      assert any(m.type == "session_init" for m in sent)


async def test_handle_resume_approval(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.resume_approval = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="user", content="hello"),
          ChatMessage(
            role="tool",
            content=json.dumps([
              {
                "type": "tool-approval-response",
                "toolCallId": "call_1",
                "approved": True,
              }
            ]),
          ),
        ],
      )
      await handler.handle(msg)

      orc.resume_approval.assert_called_once()
      call_kwargs = orc.resume_approval.call_args.kwargs
      assert call_kwargs["session_id"] == "sess-1"
      assert call_kwargs["approvals"] == {"call_1": {"approved": True}}


async def test_handle_with_external_tools_executes(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.execute_with_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[ChatMessage(role="user", content="run ls")],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      orc.execute_with_external_tools.assert_called_once()
      call_kwargs = orc.execute_with_external_tools.call_args.kwargs
      assert call_kwargs["prompt"] == "run ls"
      assert call_kwargs["working_dir"] == handler._working_dir
      assert len(call_kwargs["tools"]) == 1
      assert call_kwargs["tools"][0].name == "bash"


async def test_handle_with_external_tools_resumes(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.resume_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="user", content="run ls"),
          ChatMessage(
            role="tool",
            content="total 0",
            tool_call_id="call_1",
          ),
        ],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      orc.resume_external_tools.assert_called_once()
      call_kwargs = orc.resume_external_tools.call_args.kwargs
      assert call_kwargs["session_id"] == "sess-1"
      assert call_kwargs["results"] == {"call_1": "total 0"}
      assert len(call_kwargs["tools"]) == 1


async def test_handle_with_external_tools_passes_host_system_prompt(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.execute_with_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="system", content="Host prompt."),
          ChatMessage(role="user", content="run ls"),
        ],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      call_kwargs = orc.execute_with_external_tools.call_args.kwargs
      assert call_kwargs["host_system_prompt"] == "Host prompt."


def test_extract_tool_results_filters_unknown_ids():
  msg = ChatRequest(
    messages=[
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call_1", name="bash", arguments="{}")],
      ),
      ChatMessage(role="tool", content="ok", tool_call_id="call_1"),
      ChatMessage(role="tool", content="orphan", tool_call_id="call_999"),
    ]
  )
  results = ChatRequestHandler._extract_tool_results(msg)
  assert results == {"call_1": "ok"}


def test_extract_tool_results_accepts_all_when_no_pending_ids():
  msg = ChatRequest(
    messages=[
      ChatMessage(role="assistant", content="Hi."),
      ChatMessage(role="tool", content="ok", tool_call_id="call_1"),
    ]
  )
  results = ChatRequestHandler._extract_tool_results(msg)
  assert results == {"call_1": "ok"}


async def async_empty() -> AsyncIterator[None]:
  if False:
    yield None
