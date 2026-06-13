"""Tests for UI rendering layer."""

from __future__ import annotations

from io import StringIO

import pytest
from nonoka.core.runner import StreamEvent
from rich.console import Console

from nonoka_cli.ui.renderer import Renderer


def _make_renderer():
  """Create a test renderer that writes to an in-memory buffer."""
  output = StringIO()
  console = Console(file=output, force_terminal=False, width=80)
  return Renderer(console=console, use_live=False), output


class TestRendererEventHandling:
  """Tests for Renderer streaming event handling."""

  @pytest.mark.asyncio
  async def test_content_delta_writes_text(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="content_delta", data={"content": "Hello"})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "Hello" in output.getvalue()

  @pytest.mark.asyncio
  async def test_content_delta_empty_content(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="content_delta", data={"content": ""})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    # Finalized empty content still renders a panel.
    assert output.getvalue() != ""

  @pytest.mark.asyncio
  async def test_content_delta_missing_content_key(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="content_delta", data={})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert output.getvalue() != ""

  @pytest.mark.asyncio
  async def test_tool_call_start_writes_tool_name(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="tool_call_start",
        data={"tool_calls": [{"name": "get_weather"}]},
      )
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "get_weather" in output.getvalue()

  @pytest.mark.asyncio
  async def test_tool_call_start_multiple_tools(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="tool_call_start",
        data={"tool_calls": [
          {"name": "tool_a"},
          {"name": "tool_b"},
        ]},
      )
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "tool_a" in output.getvalue()
    assert "tool_b" in output.getvalue()

  @pytest.mark.asyncio
  async def test_tool_call_result_success(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="tool_call_start",
        data={"tool_calls": [{"id": "tc-1", "function": {"name": "x"}}]},
      )
      yield StreamEvent(
        type="tool_call_result",
        data={"tool_call_id": "tc-1", "name": "x", "result_preview": "ok", "is_error": False},
      )
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "ok" in output.getvalue()
    assert "Done" in output.getvalue()

  @pytest.mark.asyncio
  async def test_tool_call_result_error(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="tool_call_result",
        data={
          "tool_call_id": "tc-1",
          "name": "x",
          "result_preview": "file not found",
          "is_error": True,
        },
      )
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "file not found" in output.getvalue()
    assert "Error" in output.getvalue()

  @pytest.mark.asyncio
  async def test_error_event(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="error",
        data={"error": "API rate limit", "error_type": "llm_error"},
      )
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "API rate limit" in output.getvalue()

  @pytest.mark.asyncio
  async def test_error_event_without_type(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="error", data={"error": "oops"})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "oops" in output.getvalue()

  @pytest.mark.asyncio
  async def test_final_event_renders_stats(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="final", data={"success": True})

    await renderer.render_stream(event_gen())
    assert "Run Stats" in output.getvalue()

  @pytest.mark.asyncio
  async def test_final_event_uses_nonoka_stats(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(
        type="final",
        data={
          "success": True,
          "turn_count": 3,
          "tool_call_count": 2,
          "duration_seconds": 1.23,
        },
      )

    await renderer.render_stream(event_gen())
    rendered = output.getvalue()
    assert "Turns:" in rendered
    assert "3" in rendered
    assert "Tool calls:" in rendered
    assert "2" in rendered
    assert "1.23s" in rendered
    assert renderer._turn_count == 3
    assert renderer._tool_call_count == 2
    assert renderer._duration == 1.23

  @pytest.mark.asyncio
  async def test_unknown_event_type_is_ignored(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="unknown_type", data={"x": 1})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    # Stats panel still rendered at final.
    assert "Run Stats" in output.getvalue()


class TestRendererStream:
  """Tests for Renderer.render_stream async method."""

  @pytest.mark.asyncio
  async def test_render_stream_consumes_all_events(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="content_delta", data={"content": "A"})
      yield StreamEvent(type="content_delta", data={"content": "B"})
      yield StreamEvent(type="final", data={})

    await renderer.render_stream(event_gen())
    assert "A" in output.getvalue()
    assert "B" in output.getvalue()

  @pytest.mark.asyncio
  async def test_render_stream_empty(self):
    renderer, output = _make_renderer()

    async def empty_gen():
      return
      yield  # make it a generator

    await renderer.render_stream(empty_gen())
    assert output.getvalue() == ""


class TestRendererClear:
  """Tests for clear_current_output."""

  @pytest.mark.asyncio
  async def test_clear_resets_state(self):
    renderer, output = _make_renderer()

    async def event_gen():
      yield StreamEvent(type="content_delta", data={"content": "Hello"})

    await renderer.render_stream(event_gen())
    renderer.clear_current_output()
    assert renderer._content.text == ""


class TestRendererDefaultConsole:
  """Tests for default console handling."""

  def test_default_console_created(self):
    renderer = Renderer(use_live=False)
    assert renderer._console is not None

  def test_custom_console(self):
    console = Console(file=StringIO(), force_terminal=False)
    renderer = Renderer(console=console, use_live=False)
    assert renderer._console is console
