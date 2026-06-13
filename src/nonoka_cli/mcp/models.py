"""Data models for nonoka-cli MCP management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class MCPStatus:
  """Runtime status of a single MCP server."""

  name: str
  status: str  # connecting / connected / error / restarting / stopped
  transport: str
  tool_count: int
  last_ping: datetime | None
  restart_count: int
  error: str | None
