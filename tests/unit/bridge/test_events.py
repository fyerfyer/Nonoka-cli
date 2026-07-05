from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.events import translate_stream_event
from nonoka_cli.bridge.protocol import (
  ApprovalRequestEvent,
  ErrorEvent,
  FinishEvent,
  TextDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
)


def test_translate_content_delta():
  event = StreamEvent(type="content_delta", data={"content": "hello"})
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], TextDeltaEvent)
  assert messages[0].text == "hello"


def test_translate_empty_content_delta():
  event = StreamEvent(type="content_delta", data={"content": ""})
  assert translate_stream_event(event) == []


def test_translate_error():
  event = StreamEvent(type="error", data={"error": "something failed"})
  messages = translate_stream_event(event)
  assert len(messages) == 2
  assert isinstance(messages[0], ErrorEvent)
  assert messages[0].message == "something failed"
  assert isinstance(messages[1], FinishEvent)
  assert messages[1].finish_reason == "error"


def test_translate_final_success():
  event = StreamEvent(type="final", data={"success": True})
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], FinishEvent)
  assert messages[0].finish_reason == "stop"


def test_translate_final_failure():
  event = StreamEvent(type="final", data={"success": False})
  messages = translate_stream_event(event)
  assert isinstance(messages[0], FinishEvent)
  assert messages[0].finish_reason == "error"


def test_translate_final_requires_approval():
  event = StreamEvent(
    type="final",
    data={"success": False, "requires_approval": True},
  )
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], FinishEvent)
  assert messages[0].finish_reason == "approval_required"


def test_translate_final_requires_external_execution():
  event = StreamEvent(
    type="final",
    data={"success": False, "requires_external_execution": True},
  )
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], FinishEvent)
  assert messages[0].finish_reason == "tool_calls"


def test_translate_tool_call_start():
  event = StreamEvent(
    type="tool_call_start",
    data={
      "tool_calls": [
        {
          "id": "call_1",
          "function": {"name": "read_file", "arguments": '{"path": "/tmp/x"}'},
        }
      ]
    },
  )
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], ToolCallEvent)
  assert messages[0].tool_call_id == "call_1"
  assert messages[0].tool_name == "read_file"
  assert messages[0].args == {"path": "/tmp/x"}


def test_translate_tool_call_result():
  event = StreamEvent(
    type="tool_call_result",
    data={
      "tool_call_id": "call_1",
      "name": "read_file",
      "result_preview": "hello",
      "result": {"content": "hello"},
      "is_error": False,
    },
  )
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], ToolResultEvent)
  assert messages[0].tool_call_id == "call_1"
  assert messages[0].content == "hello"
  assert messages[0].result == {"content": "hello"}
  assert messages[0].is_error is False


def test_translate_approval_request():
  event = StreamEvent(
    type="approval_request",
    data={
      "tool_call_id": "call_1",
      "tool_name": "write_file",
      "args": {"path": "/tmp/x", "content": "hi"},
    },
  )
  messages = translate_stream_event(event)
  assert len(messages) == 1
  assert isinstance(messages[0], ApprovalRequestEvent)
  assert messages[0].id == "call_1"
  assert messages[0].tool_call_id == "call_1"
  assert messages[0].tool_name == "write_file"
  assert messages[0].args == {"path": "/tmp/x", "content": "hi"}
