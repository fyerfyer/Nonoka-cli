"""Built-in tools for nonoka-cli."""

from __future__ import annotations

from nonoka.core.types import Capability

from nonoka_cli.tools.builtins import agent_tools, file_tools


def get_tools() -> list[Capability]:
  """Return all built-in tools."""
  return file_tools.get_tools() + agent_tools.get_tools()
