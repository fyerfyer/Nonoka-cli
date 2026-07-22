import json

import pytest

from nonoka_cli.bridge.protocol import (
  CancelRequest,
  ChatRequest,
  ErrorEvent,
  ExternalToolDefinition,
  FinishEvent,
  SessionInitEvent,
  TextDeltaEvent,
  encode_outbound_message,
  parse_inbound_line,
)


def test_parse_chat_request():
  line = json.dumps(
    {
      "type": "chat",
      "messages": [{"role": "user", "content": "hello"}],
      "session_id": "sess-1",
      "cwd": "/tmp",
    }
  )
  msg = parse_inbound_line(line)
  assert isinstance(msg, ChatRequest)
  assert msg.messages[0].content == "hello"
  assert msg.session_id == "sess-1"
  assert msg.cwd == "/tmp"


def test_parse_chat_request_with_tools():
  line = json.dumps(
    {
      "type": "chat",
      "messages": [{"role": "user", "content": "run ls"}],
      "tools": [
        {
          "name": "bash",
          "description": "Run shell commands",
          "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
          },
        }
      ],
      "cwd": "/tmp",
    }
  )
  msg = parse_inbound_line(line)
  assert isinstance(msg, ChatRequest)
  assert msg.tools is not None
  assert len(msg.tools) == 1
  assert msg.tools[0].name == "bash"
  assert msg.tools[0].description == "Run shell commands"
  assert "command" in msg.tools[0].parameters["properties"]


def test_parse_structured_external_tool_receipt():
  msg = parse_inbound_line(json.dumps({
    "type": "chat", "cwd": "/tmp",
    "messages": [{
      "role": "tool", "content": "done", "tool_call_id": "call-1",
      "result": {"result": "done", "host": "opencode", "workspace": {
        "root": "/tmp", "before_digest": "a", "after_digest": "b",
      }},
    }],
  }))
  assert isinstance(msg, ChatRequest)
  assert msg.messages[0].result["workspace"]["after_digest"] == "b"


def test_external_tool_definition_roundtrip():
  tool = ExternalToolDefinition(
    name="write_file",
    description="Write a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
  )
  data = json.loads(tool.model_dump_json())
  assert data["name"] == "write_file"
  assert data["parameters"]["properties"]["path"]["type"] == "string"


def test_parse_unknown_type():
  line = json.dumps({"type": "unknown"})
  with pytest.raises(ValueError, match="Unknown inbound message type"):
    parse_inbound_line(line)


def test_parse_invalid_json():
  with pytest.raises(ValueError):
    parse_inbound_line("not json")


def test_parse_cancel_request():
  msg = parse_inbound_line('{"type":"cancel","request_id":"req-1"}')
  assert isinstance(msg, CancelRequest)
  assert msg.request_id == "req-1"


def test_encode_text_delta():
  msg = TextDeltaEvent(text="hi")
  assert encode_outbound_message(msg) == '{"type":"text_delta","text":"hi"}'


def test_encode_finish():
  msg = FinishEvent(finish_reason="stop")
  data = json.loads(encode_outbound_message(msg))
  assert data == {"type": "finish", "finish_reason": "stop"}


def test_encode_error():
  msg = ErrorEvent(message="boom")
  data = json.loads(encode_outbound_message(msg))
  assert data == {"type": "error", "message": "boom"}


def test_encode_session_init():
  msg = SessionInitEvent(session_id="abc")
  data = json.loads(encode_outbound_message(msg))
  assert data == {"type": "session_init", "session_id": "abc"}
