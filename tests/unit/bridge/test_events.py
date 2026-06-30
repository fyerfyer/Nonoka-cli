from nonoka.core.runner import StreamEvent

from nonoka_cli.bridge.events import translate_stream_event
from nonoka_cli.bridge.protocol import ErrorEvent, FinishEvent, TextDeltaEvent


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


def test_translate_tool_events_ignored():
  start = StreamEvent(type="tool_call_start", data={"name": "x"})
  result = StreamEvent(type="tool_call_result", data={"content": "y"})
  assert translate_stream_event(start) == []
  assert translate_stream_event(result) == []
