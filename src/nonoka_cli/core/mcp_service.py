"""MCP server lifecycle service for nonoka-cli."""

from __future__ import annotations

import structlog

from nonoka_cli.config.loader import ConfigLoader
from nonoka_cli.config.models import CLIConfig, MCPServerConfigModel
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.core.mcp")


class MCPService:
  """Encapsulate MCP server status and runtime registration."""

  def __init__(self, manager: MCPManager, agent_factory: AgentFactory | None = None):
    self._manager = manager
    self._agent_factory = agent_factory

  def list_status(self) -> dict[str, MCPStatus]:
    """Return the status of all configured MCP servers."""
    return self._manager.list_status()

  async def restart(self, name: str) -> MCPStatus:
    """Restart a configured MCP server."""
    await self._manager.restart(name)
    return self._manager.get_status(name)

  async def add_server(
    self,
    name: str,
    config: MCPServerConfigModel,
    runtime_config: CLIConfig,
  ) -> MCPStatus:
    """Persist, start, and register a new MCP server at runtime."""
    mcp_servers = ConfigLoader.load_mcp_servers()
    if name in mcp_servers:
      raise ConfigError(f"MCP server '{name}' already exists.")
    mcp_servers[name] = config.model_dump()
    ConfigLoader.save_mcp_servers(mcp_servers)

    await self._manager.start_server(name, config)
    if self._agent_factory is not None:
      self._agent_factory.rebuild()

    runtime_config.mcp_servers[name] = config
    logger.info("mcp_server_added", name=name, status="connected")
    return self._manager.get_status(name)
