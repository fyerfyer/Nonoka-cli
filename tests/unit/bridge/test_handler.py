"""Tests for ChatRequestHandler."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.bridge.handler import ChatRequestHandler
from nonoka_cli.bridge.protocol import ChatRequest, ChatMessage


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


async def async_empty() -> AsyncIterator[None]:
  if False:
    yield None
