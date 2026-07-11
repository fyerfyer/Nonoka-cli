"""Tests for the turn-based context trimmer."""

from __future__ import annotations

import pytest
from nonoka.core.llm import LLMMessage, LLMMessageRole

from nonoka_cli.core.context_trimmer import (
  ContextTrimConfig,
  TurnBasedContextTrimmer,
)


def _msg(role: LLMMessageRole, content: str, **kwargs) -> LLMMessage:
  return LLMMessage(role=role, content=content, **kwargs)


def test_trimmer_keeps_all_messages_when_under_budget():
  messages = [
    _msg(LLMMessageRole.USER, "first"),
    _msg(LLMMessageRole.ASSISTANT, "ok"),
    _msg(LLMMessageRole.USER, "second"),
  ]
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(max_turns=3))
  assert trimmer.trim(messages) == messages


def test_trimmer_keeps_last_n_user_turns():
  messages = [
    _msg(LLMMessageRole.USER, "u1"),
    _msg(LLMMessageRole.ASSISTANT, "a1"),
    _msg(LLMMessageRole.TOOL, "t1", name="read", tool_call_id="tc1"),
    _msg(LLMMessageRole.USER, "u2"),
    _msg(LLMMessageRole.ASSISTANT, "a2"),
    _msg(LLMMessageRole.USER, "u3"),
  ]
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(max_turns=2))
  trimmed = trimmer.trim(messages)
  assert trimmed == messages[3:]


def test_trimmer_preserves_complete_tool_chain():
  messages = [
    _msg(LLMMessageRole.USER, "u1"),
    _msg(LLMMessageRole.ASSISTANT, "a1", tool_calls=[{"id": "tc1"}]),
    _msg(LLMMessageRole.TOOL, "t1", name="read", tool_call_id="tc1"),
    _msg(LLMMessageRole.USER, "u2"),
    _msg(LLMMessageRole.ASSISTANT, "a2"),
    _msg(LLMMessageRole.TOOL, "t2", name="read", tool_call_id="tc2"),
    _msg(LLMMessageRole.USER, "u3"),
  ]
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(max_turns=2))
  trimmed = trimmer.trim(messages)
  # Should start at the second user turn to keep assistant+tool together.
  assert trimmed[0].content == "u2"
  assert len(trimmed) == 4


def test_trimmer_drops_synthetic_user_messages():
  messages = [
    _msg(LLMMessageRole.USER, "u1"),
    _msg(LLMMessageRole.USER, "summary", metadata={"synthetic": True}),
    _msg(LLMMessageRole.USER, "u2"),
  ]
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(max_turns=1))
  trimmed = trimmer.trim(messages)
  assert trimmed[0].content == "u2"


def test_trimmer_disabled_returns_copy():
  messages = [
    _msg(LLMMessageRole.USER, "u1"),
    _msg(LLMMessageRole.USER, "u2"),
  ]
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(enabled=False))
  trimmed = trimmer.trim(messages)
  assert trimmed == messages
  assert trimmed is not messages


def test_trimmer_from_config_dict():
  trimmer = TurnBasedContextTrimmer.from_config({"max_turns": 4, "enabled": False})
  assert trimmer.config.max_turns == 4
  assert trimmer.config.enabled is False


def test_trimmer_max_turns_clamped():
  trimmer = TurnBasedContextTrimmer(ContextTrimConfig(max_turns=0))
  assert trimmer.config.max_turns == 1


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
