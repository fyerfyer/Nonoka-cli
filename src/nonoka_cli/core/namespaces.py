"""Tool namespace helpers for nonoka-cli.

Centralizes the rules used to prefix skill/MCP tools so they do not collide
with host native tools or with each other when running in host-managed
(external-tools) mode.
"""

from __future__ import annotations

import re
from typing import Any


# OpenAI function names must match ^[a-zA-Z0-9_-]+$. We encode ``:`` (the
# natural namespace separator) as ``__`` and replace any other invalid
# characters with underscores. Double underscores are preserved as the
# namespace marker.
NAMESPACE_SEPARATOR = "__"


def sanitize_tool_name(name: str) -> str:
  """Return a provider-safe tool name.

  OpenAI function names must match ``^[a-zA-Z0-9_-]+$``. We replace namespace
  separators (``:``) with ``__`` and replace any remaining invalid characters
  with underscores. Double underscores are preserved as the namespace marker.
  """
  sanitized = name.replace(":", NAMESPACE_SEPARATOR)
  sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", sanitized)
  # Avoid leading/trailing underscores.
  return sanitized.strip("_")


def mcp_tool_name(server_name: str, tool_name: str) -> str:
  """Return the prefixed name for an MCP tool."""
  return sanitize_tool_name(f"mcp__{server_name}__{tool_name}")


def skill_tool_name(skill_name: str, tool_name: str) -> str:
  """Return the prefixed name for a skill tool."""
  return sanitize_tool_name(f"skill__{skill_name}__{tool_name}")


class PrefixedCapability:
  """Wraps a capability with a namespace prefix to avoid name collisions.

  This is used for MCP tools (``mcp__<server>__<tool>``) and skill tools
  (``skill__<skill>__<tool>``) when they are exposed alongside host native
  tools in external-tools mode.
  """

  def __init__(self, wrapped: Any, prefix: str):
    self._wrapped = wrapped
    self.name = sanitize_tool_name(f"{prefix}{wrapped.name}")
    self.description = getattr(wrapped, "description", "")
    self.parameters = getattr(wrapped, "parameters", {})
    self.external = getattr(wrapped, "external", False)
    self.execution = getattr(wrapped, "execution", None)
    self.metadata = dict(getattr(wrapped, "metadata", {}) or {})

  async def invoke(self, ctx: Any, arguments: dict[str, Any]) -> Any:
    return await self._wrapped.invoke(ctx, arguments)

  def to_json_schema(self) -> dict[str, Any]:
    schema = self._wrapped.to_json_schema()
    if not isinstance(schema, dict):
      schema = {}
    if (
      schema.get("type") == "function"
      and isinstance(schema.get("function"), dict)
    ):
      schema["function"]["name"] = self.name
    return schema

  def __getattr__(self, name: str) -> Any:
    """Forward any unknown attributes to the wrapped capability."""
    if name == "_wrapped":
      raise AttributeError(name)
    return getattr(self._wrapped, name)
