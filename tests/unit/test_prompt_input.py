"""Tests for the prompt-toolkit input layer."""

from __future__ import annotations

from unittest.mock import patch

import pytest
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
      pt = PromptInput(registry, input=inp, output=DummyOutput())
      result = await pt.read()

    assert result == "hello world"

  @pytest.mark.asyncio
  async def test_strips_whitespace(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("  hello  \n")
      pt = PromptInput(registry, input=inp, output=DummyOutput())
      result = await pt.read()

    assert result == "hello"

  @pytest.mark.asyncio
  async def test_raises_eof_from_prompt_session(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      pt = PromptInput(registry, input=inp, output=DummyOutput())
      with patch.object(pt._session, "prompt_async", side_effect=EOFError):
        with pytest.raises(EOFError):
          await pt.read()

  @pytest.mark.asyncio
  async def test_empty_after_strip_returns_empty_string(self, registry: CommandRegistry):
    with create_pipe_input() as inp:
      inp.send_text("   \n")
      pt = PromptInput(registry, input=inp, output=DummyOutput())
      result = await pt.read()

    assert result == ""
