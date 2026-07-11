"""Context trimming for long-running agent sessions.

Inspired by the OpenAI Agents SDK ``TrimmingSession`` pattern:
keep only the last N complete user turns, where a turn starts at a user
message and includes all assistant/tool messages up to (but not including)
the next user message.  This preserves the immediate tool-call chains while
preventing older turns from consuming the context window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nonoka.core.llm import LLMMessage, LLMMessageRole


@dataclass
class ContextTrimConfig:
  """Configuration for turn-based context trimming."""

  max_turns: int = 8
  """Maximum number of complete user turns to retain verbatim."""

  enabled: bool = True
  """Whether trimming is active."""

  max_tokens: int | None = None
  """Optional token budget (reserved for future token-aware trimming)."""

  def __post_init__(self):
    if self.max_turns < 1:
      self.max_turns = 1


class TurnBasedContextTrimmer:
  """Trim a list of LLM messages to the last N user turns.

  A "turn" begins at a real user message and includes every message after it
  until the next real user message.  The trimmer keeps complete turns so that
  an assistant message with pending tool_calls is never separated from its
  corresponding tool results.
  """

  def __init__(self, config: ContextTrimConfig | None = None):
    self._config = config or ContextTrimConfig()

  @property
  def config(self) -> ContextTrimConfig:
    return self._config

  def trim(self, messages: list[LLMMessage]) -> list[LLMMessage]:
    """Return a new list containing only the last ``max_turns`` user turns.

    If trimming is disabled or the message list is already within the budget,
    a shallow copy of the original list is returned unchanged.
    """
    if not self._config.enabled or not messages:
      return list(messages)

    user_indices = [
      i for i, m in enumerate(messages) if self._is_real_user_message(m)
    ]

    if len(user_indices) <= self._config.max_turns:
      return list(messages)

    # Keep everything from the start of the Nth-most-recent user turn onward.
    cutoff = user_indices[-self._config.max_turns]
    return list(messages[cutoff:])

  @staticmethod
  def _is_real_user_message(message: LLMMessage) -> bool:
    """Return True if *message* is a user message (not a synthetic summary)."""
    if message.role != LLMMessageRole.USER:
      return False
    # Synthetic summary prompts are tagged in metadata by the summarizer.
    meta = getattr(message, "metadata", None) or {}
    return not meta.get("synthetic", False)

  @classmethod
  def from_config(
    cls,
    data: dict[str, Any] | ContextTrimConfig | None,
  ) -> "TurnBasedContextTrimmer":
    """Build a trimmer from a config dict or object."""
    if data is None:
      return cls()
    if isinstance(data, ContextTrimConfig):
      return cls(data)
    return cls(ContextTrimConfig(**data))
