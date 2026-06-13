"""Tests for the prompt-toolkit input layer."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from nonoka_cli.shell.commands import CommandRegistry
from nonoka_cli.shell.prompt_input import PromptInput


@pytest.fixture
def registry():
  """Return an empty command registry."""
  return CommandRegistry()


class TestPromptInputReading:
  """Tests for PromptInput.read()."""

  @pytest.mark.asyncio
  async def test_reads_input(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("hello world\n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == "hello world"

  @pytest.mark.asyncio
  async def test_strips_whitespace(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("  hello  \n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == "hello"

  @pytest.mark.asyncio
  async def test_raises_eof_from_prompt_session(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      with patch.object(pt._session, "prompt_async", side_effect=EOFError):
        with pytest.raises(EOFError):
          await pt.read()

  @pytest.mark.asyncio
  async def test_empty_after_strip_returns_empty_string(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("   \n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == ""


class TestPromptInputMultiline:
  """Tests for multi-line input detection."""

  @pytest.mark.asyncio
  async def test_multiline_trigger_triple_quote(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text('"""\nline one\nline two\n"""\n')
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == '"""\nline one\nline two\n"""'

  @pytest.mark.asyncio
  async def test_multiline_unclosed_bracket(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("print(\n  1,\n  2\n)\n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == "print(\n  1,\n  2\n)"

  @pytest.mark.asyncio
  async def test_single_line_when_balanced(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("hello world\n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == "hello world"

  @pytest.mark.asyncio
  async def test_multiline_unclosed_single_quote(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("'hello\nworld'\n")
      pt = PromptInput(registry, input=inp, output=DummyOutput(), history=InMemoryHistory())
      result = await pt.read()

    assert result == "'hello\nworld'"


class TestPromptInputIsComplete:
  """Tests for the completeness helper."""

  def test_complete_for_balanced_input(self, registry: CommandRegistry):
    pt = PromptInput(registry, history=InMemoryHistory())
    assert pt._is_complete("hello world") is True
    assert pt._is_complete("print(1, 2)") is True
    assert pt._is_complete("'hello'") is True

  def test_incomplete_for_unclosed_triple_quote(self, registry: CommandRegistry):
    pt = PromptInput(registry, history=InMemoryHistory())
    assert pt._is_complete('"""hello') is False
    assert pt._is_complete('"""hello"""') is True

  def test_incomplete_for_unclosed_bracket(self, registry: CommandRegistry):
    pt = PromptInput(registry, history=InMemoryHistory())
    assert pt._is_complete("print(") is False
    assert pt._is_complete("print()") is True

  def test_mismatched_closing_bracket_treated_as_complete(self, registry: CommandRegistry):
    pt = PromptInput(registry, history=InMemoryHistory())
    assert pt._is_complete(")") is True
