"""Session data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SessionInfo:
  """CLI-level metadata for a conversation session.

  This is separate from nonoka's internal SessionState and is used to
  maintain a human-friendly index of sessions in the cli_sessions table.
  """

  session_id: str
  name: str | None
  model: str
  created_at: datetime
  last_active: datetime
  message_count: int
  metadata: dict[str, Any]
