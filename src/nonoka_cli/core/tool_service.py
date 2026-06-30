"""Tool management service for nonoka-cli."""

from __future__ import annotations

from typing import Any

from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.tools.loader import ToolLoader


class ToolService:
  """Encapsulate tool loading, introspection, and reload."""

  def __init__(self, agent_factory: AgentFactory, tool_loader: ToolLoader | None):
    self._agent_factory = agent_factory
    self._tool_loader = tool_loader

  def list_tools(self) -> list[Any]:
    """Return all tools available to the current Agent."""
    return self._agent_factory.list_all_tools()

  def get_tool_info(self, name: str) -> dict[str, Any] | None:
    """Return the JSON schema for a named tool, or None if not found."""
    tool = self._agent_factory.get_tool(name)
    return tool.to_json_schema() if tool is not None else None

  def reload_tools(self) -> list[Any]:
    """Reload local tools and rebuild the Agent."""
    if self._tool_loader is not None:
      self._tool_loader.reload()
    self._agent_factory.rebuild()
    return self.list_tools()
