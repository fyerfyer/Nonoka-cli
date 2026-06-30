import json

import pytest

from nonoka_cli.bridge.protocol import (
  ChatRequest,
  ErrorEvent,
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


def test_parse_unknown_type():
  line = json.dumps({"type": "unknown"})
  with pytest.raises(ValueError, match="Unknown inbound message type"):
    parse_inbound_line(line)


def test_parse_invalid_json():
  with pytest.raises(ValueError):
    parse_inbound_line("not json")


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
