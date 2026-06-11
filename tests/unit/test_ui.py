"""Tests for UI rendering layer."""

from __future__ import annotations

from io import StringIO

import pytest

from nonoka.core.runner import StreamEvent

from nonoka_cli.ui.renderer import Renderer


class TestRendererEventHandling:
  """Tests for Renderer._render_event method."""

  @pytest.fixture
  def output(self):
    return StringIO()

  @pytest.fixture
  def renderer(self, output):
    return Renderer(file=output)

  def test_content_delta_writes_text(self, renderer, output):
    event = StreamEvent(type="content_delta", data={"content": "Hello"})
    renderer._render_event(event)
    assert output.getvalue() == "Hello"

  def test_content_delta_empty_content(self, renderer, output):
    event = StreamEvent(type="content_delta", data={"content": ""})
    renderer._render_event(event)
    assert output.getvalue() == ""

  def test_content_delta_missing_content_key(self, renderer, output):
    event = StreamEvent(type="content_delta", data={})
    renderer._render_event(event)
    assert output.getvalue() == ""

  def test_tool_call_start_writes_tool_name(self, renderer, output):
    event = StreamEvent(
      type="tool_call_start",
      data={"tool_calls": [{"name": "get_weather"}]},
    )
    renderer._render_event(event)
    assert "[Tool: get_weather]" in output.getvalue()

  def test_tool_call_start_multiple_tools(self, renderer, output):
    event = StreamEvent(
      type="tool_call_start",
      data={"tool_calls": [
        {"name": "tool_a"},
        {"name": "tool_b"},
      ]},
    )
    renderer._render_event(event)
    assert "[Tool: tool_a, tool_b]" in output.getvalue()

  def test_tool_call_start_sets_flag(self, renderer, output):
    event = StreamEvent(
      type="tool_call_start",
      data={"tool_calls": [{"name": "x"}]},
    )
    renderer._render_event(event)
    assert renderer._in_tool_call is True

  def test_tool_call_result_success(self, renderer, output):
    renderer._in_tool_call = True
    event = StreamEvent(type="tool_call_result", data={"result": "ok"})
    renderer._render_event(event)
    assert "[Tool done]" in output.getvalue()
    assert renderer._in_tool_call is False

  def test_tool_call_result_error(self, renderer, output):
    renderer._in_tool_call = True
    event = StreamEvent(
      type="tool_call_result",
      data={"result": {"error": "file not found"}},
    )
    renderer._render_event(event)
    assert "[Tool error: file not found]" in output.getvalue()

  def test_error_event(self, renderer, output):
    event = StreamEvent(
      type="error",
      data={"error": "API rate limit", "error_type": "llm_error"},
    )
    renderer._render_event(event)
    assert "[LLM_ERROR] API rate limit" in output.getvalue()

  def test_error_event_without_type(self, renderer, output):
    event = StreamEvent(type="error", data={"error": "oops"})
    renderer._render_event(event)
    assert "[ERROR] oops" in output.getvalue()

  def test_final_event_adds_newline(self, renderer, output):
    event = StreamEvent(type="final", data={"success": True})
    renderer._render_event(event)
    assert output.getvalue() == "\n"

  def test_final_event_no_newline_during_tool_call(self, renderer, output):
    renderer._in_tool_call = True
    event = StreamEvent(type="final", data={"success": True})
    renderer._render_event(event)
    assert output.getvalue() == ""

  def test_unknown_event_type_is_ignored(self, renderer, output):
    event = StreamEvent(type="unknown_type", data={"x": 1})
    renderer._render_event(event)
    assert output.getvalue() == ""


class TestRendererStream:
  """Tests for Renderer.render_stream async method."""

  @pytest.mark.asyncio
  async def test_render_stream_consumes_all_events(self):
    output = StringIO()
    renderer = Renderer(file=output)

    async def event_gen():
      yield StreamEvent(type="content_delta", data={"content": "A"})
      yield StreamEvent(type="content_delta", data={"content": "B"})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert output.getvalue() == "AB\n"

  @pytest.mark.asyncio
  async def test_render_stream_empty(self):
    output = StringIO()
    renderer = Renderer(file=output)

    async def empty_gen():
      return
      yield  # make it a generator

    await renderer.render_stream(empty_gen())
    assert output.getvalue() == ""


class TestRendererClear:
  """Tests for clear_current_output."""

  def test_clear_is_noop(self):
    output = StringIO()
    renderer = Renderer(file=output)
    renderer.clear_current_output()
    # Should not raise or modify output
    assert output.getvalue() == ""


class TestRendererDefaultFile:
  """Tests for default output file."""

  def test_default_is_stdout(self):
    import sys
    renderer = Renderer()
    assert renderer._file is sys.stdout

  def test_custom_file(self):
    output = StringIO()
    renderer = Renderer(file=output)
    assert renderer._file is output
