"""MCP server lifecycle manager for nonoka-cli.

This is a thin CLI adapter around :class:`nonoka.ext.mcp.MCPManager`. All
actual lifecycle logic (startup, health checks, restart, shutdown) lives in
nonoka-agent; this module only converts Pydantic configuration models and
presents status in the CLI-specific ``MCPStatus`` format.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from nonoka.core.types import Capability
from nonoka.ext.mcp import (
  MCPConnectionError as _AgentMCPConnectionError,
)
from nonoka.ext.mcp import (
  MCPManager as _AgentMCPManager,
)
from nonoka.ext.mcp import (
  MCPRestartExhaustedError as _AgentMCPRestartExhaustedError,
)
from nonoka.ext.mcp import (
  MCPServerConfig,
)

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.utils.errors import MCPConnectionError, MCPRestartExhaustedError


_STDIO_RUNTIME_ENV_NAMES = (
  # The MCP SDK intentionally starts stdio servers with a restricted default
  # environment.  Keep the same safety property while preserving the routing,
  # TLS, and npm-cache state that an outer SRT process-tree sandbox injects.
  # Without these values, an `npx` MCP bypasses SRT's proxy and either retries
  # DNS until its startup timeout or writes its cache under `~/.npm`.
  "NPM_CONFIG_CACHE",
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "NO_PROXY",
  "http_proxy",
  "https_proxy",
  "all_proxy",
  "no_proxy",
  "SSL_CERT_FILE",
  "NODE_EXTRA_CA_CERTS",
)


def _stdio_runtime_env() -> dict[str, str] | None:
  """Return the minimal inherited environment required by stdio MCPs.

  This deliberately does not forward provider keys or arbitrary user process
  state.  MCP SDK merges this mapping with its own safe default environment.
  """
  env = {
    name: value
    for name in _STDIO_RUNTIME_ENV_NAMES
    if (value := os.environ.get(name)) is not None
  }
  return env or None


def _to_agent_config(config: MCPServerConfigModel) -> MCPServerConfig:
  """Convert CLI Pydantic config to nonoka-agent's dataclass."""
  return MCPServerConfig(
    transport=config.transport,  # type: ignore[arg-type]
    command=config.command,
    args=list(config.args),
    env=_stdio_runtime_env() if config.transport == "stdio" else None,
  )


def _to_cli_status(status: Any) -> MCPStatus:
  """Convert nonoka-agent server status to CLI status model."""
  return MCPStatus(
    name=status.name,
    status=status.status,
    transport=status.transport,
    tool_count=status.tool_count,
    last_ping=status.last_ping,
    restart_count=status.restart_count,
    error=status.error,
  )


class MCPManager:
  """CLI-facing facade for the nonoka-agent MCP lifecycle manager."""

  def __init__(self):
    """Initialize an empty manager."""
    self._inner = _AgentMCPManager()
    self._startup_errors: dict[str, str] = {}

  async def start_all(
    self,
    configs: dict[str, MCPServerConfigModel],
  ) -> list[tuple[str, Capability]]:
    """Start all configured MCP servers in parallel.

    Returns:
      A list of ``(server_name, capability)`` pairs.

    Raises:
      MCPRestartExhaustedError: If one or more servers fail to start.
    """
    if not configs:
      return []
    agent_configs = {
      name: _to_agent_config(cfg) for name, cfg in configs.items()
    }
    timeout = min(config.startup_timeout_seconds for config in configs.values())
    try:
      result = await asyncio.wait_for(self._inner.start_all(agent_configs), timeout=timeout)
      for name in configs:
        self._startup_errors.pop(name, None)
      return result
    except asyncio.TimeoutError as exc:
      # `npx` can spend over a minute retrying DNS before it writes anything to
      # stdio. A bounded timeout keeps `/reload` responsive and lets callers
      # see a lifecycle error instead of a blank TUI.
      await self._inner.stop_all()
      error = f"MCP startup timed out after {timeout:g}s; check network/package logs."
      self._startup_errors.update({name: error for name in configs})
      raise MCPRestartExhaustedError(error) from exc
    except _AgentMCPRestartExhaustedError as exc:
      raise MCPRestartExhaustedError(str(exc)) from exc

  async def start_server(
    self,
    name: str,
    config: MCPServerConfigModel,
  ) -> list[tuple[str, Capability]]:
    """Start a single MCP server and add it to the managed pool."""
    try:
      result = await asyncio.wait_for(
        self._inner.start_server(name, _to_agent_config(config)),
        timeout=config.startup_timeout_seconds,
      )
      self._startup_errors.pop(name, None)
      return result
    except asyncio.TimeoutError as exc:
      await self._inner.stop_all()
      error = (
        f"MCP server '{name}' startup timed out after {config.startup_timeout_seconds:g}s; "
        "check network/package logs."
      )
      self._startup_errors[name] = error
      raise MCPRestartExhaustedError(error) from exc
    except _AgentMCPRestartExhaustedError as exc:
      raise MCPRestartExhaustedError(str(exc)) from exc

  async def restart(self, name: str) -> list[tuple[str, Capability]]:
    """Restart a single MCP server."""
    try:
      return await self._inner.restart(name)
    except _AgentMCPRestartExhaustedError as exc:
      raise MCPRestartExhaustedError(str(exc)) from exc

  async def stop_all(self) -> None:
    """Gracefully stop all MCP servers."""
    await self._inner.stop_all()

  def get_status(self, name: str) -> MCPStatus:
    """Return the current status for a named server."""
    try:
      return _to_cli_status(self._inner.get_status(name))
    except _AgentMCPConnectionError as exc:
      raise MCPConnectionError(str(exc)) from exc

  def list_status(self) -> dict[str, MCPStatus]:
    """Return a snapshot of all server statuses."""
    statuses = {name: _to_cli_status(s) for name, s in self._inner.list_status().items()}
    for name, error in self._startup_errors.items():
      statuses[name] = MCPStatus(
        name=name,
        status="error",
        transport="stdio",
        tool_count=0,
        last_ping=None,
        restart_count=0,
        error=error,
      )
    return statuses

  def get_tools(self) -> list[tuple[str, Capability]]:
    """Return all currently available MCP capabilities with server names."""
    return self._inner.get_tools()
