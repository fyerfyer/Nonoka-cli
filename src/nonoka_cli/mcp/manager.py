"""MCP server lifecycle manager for nonoka-cli.

Wraps nonoka's ``MCPClient`` to provide:
- Parallel startup of configured MCP servers
- Automatic tool discovery and registration
- Periodic health checks via MCP ping
- Exponential-backoff restart on failure
- Graceful shutdown of all server processes
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import structlog
from nonoka.core.types import Capability
from nonoka.ext.mcp import MCPClient

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.mcp.models import MCPStatus
from nonoka_cli.utils.errors import MCPConnectionError, MCPRestartExhaustedError

logger = structlog.get_logger("nonoka_cli.mcp")


class MCPManager:
  """Manages the lifecycle of configured MCP servers and exposes their tools."""

  _HEALTH_INTERVAL_SECONDS = 30.0
  _MAX_RESTART_ATTEMPTS = 3
  _BACKOFF_BASE_SECONDS = 2.0

  def __init__(self):
    """Initialize an empty manager."""
    self._configs: dict[str, MCPServerConfigModel] = {}
    self._clients: dict[str, MCPClient] = {}
    self._status: dict[str, MCPStatus] = {}
    self._tools: list[Capability] = []
    self._restart_counts: dict[str, int] = {}
    self._health_task: asyncio.Task | None = None
    self._stop_event = asyncio.Event()

  # ------------------------------------------------------------------ #
  # Lifecycle
  # ------------------------------------------------------------------ #

  async def start_all(
    self,
    configs: dict[str, MCPServerConfigModel],
  ) -> list[Capability]:
    """Start all configured MCP servers in parallel.

    Args:
      configs: Mapping from server name to its configuration.

    Returns:
      All capabilities discovered from successfully started servers.

    Raises:
      MCPRestartExhaustedError: If a server fails after all restart attempts.
        Servers that succeeded are still available.
    """
    self._configs = configs
    self._stop_event.clear()

    results = await asyncio.gather(
      *[self._start_one(name, cfg) for name, cfg in configs.items()],
      return_exceptions=True,
    )

    all_tools: list[Capability] = []
    failed: list[str] = []
    for name, result in zip(configs.keys(), results):
      if isinstance(result, BaseException):
        logger.error("mcp_server_failed_to_start", name=name, error=str(result))
        failed.append(name)
        self._status[name] = MCPStatus(
          name=name,
          status="error",
          transport=configs[name].transport,
          tool_count=0,
          last_ping=None,
          restart_count=self._restart_counts.get(name, 0),
          error=str(result),
        )
      else:
        all_tools.extend(result)

    # Refresh the merged tool list.
    self._tools = all_tools

    # Start background health checks if any servers are running.
    if self._clients:
      self._health_task = asyncio.create_task(self._health_check_loop())

    if failed:
      raise MCPRestartExhaustedError(
        f"Failed to start MCP server(s): {', '.join(failed)}"
      )

    logger.info("mcp_servers_started", count=len(self._clients), tool_count=len(all_tools))
    return all_tools

  async def start_server(
    self,
    name: str,
    config: MCPServerConfigModel,
  ) -> list[Capability]:
    """Start a single MCP server and add it to the managed pool.

    Args:
      name: Server name.
      config: Server configuration.

    Returns:
      The capabilities exposed by the newly started server.

    Raises:
      MCPRestartExhaustedError: If the server fails to start.
    """
    self._configs[name] = config
    tools = await self._start_one(name, config)
    self._tools.extend(tools)

    # Start health checks if this is the first server.
    if self._health_task is None and self._clients:
      self._health_task = asyncio.create_task(self._health_check_loop())

    logger.info("mcp_server_added", name=name, tool_count=len(tools))
    return tools

  async def _start_one(
    self,
    name: str,
    config: MCPServerConfigModel,
  ) -> list[Capability]:
    """Start a single MCP server and return its capabilities."""
    self._status[name] = MCPStatus(
      name=name,
      status="connecting",
      transport=config.transport,
      tool_count=0,
      last_ping=None,
      restart_count=self._restart_counts.get(name, 0),
      error=None,
    )

    client = MCPClient(
      transport=config.transport,
      command=config.command,
      args=list(config.args),
    )
    await client.connect()
    tools = await client.get_capabilities()

    self._clients[name] = client
    self._status[name] = MCPStatus(
      name=name,
      status="connected",
      transport=config.transport,
      tool_count=len(tools),
      last_ping=datetime.now(),
      restart_count=self._restart_counts.get(name, 0),
      error=None,
    )

    logger.info(
      "mcp_server_connected",
      name=name,
      transport=config.transport,
      tool_count=len(tools),
    )
    return tools

  async def restart(self, name: str) -> list[Capability]:
    """Restart a single MCP server.

    Args:
      name: Server name as declared in configuration.

    Returns:
      The new capabilities exposed by the restarted server.

    Raises:
      MCPConnectionError: If the server is not configured.
      MCPRestartExhaustedError: If restart attempts are exhausted.
    """
    if name not in self._configs:
      raise MCPConnectionError(f"MCP server '{name}' is not configured.")

    self._restart_counts[name] = self._restart_counts.get(name, 0) + 1
    restart_count = self._restart_counts[name]

    self._status[name] = MCPStatus(
      name=name,
      status="restarting",
      transport=self._configs[name].transport,
      tool_count=0,
      last_ping=None,
      restart_count=restart_count,
      error=None,
    )

    # Remove old client's tools from the merged list.
    await self._disconnect_one(name)

    config = self._configs[name]
    last_error: BaseException | None = None
    attempts = min(restart_count, self._MAX_RESTART_ATTEMPTS)

    for attempt in range(attempts + 1):
      try:
        tools = await self._start_one(name, config)
        self._rebuild_tool_list()
        logger.info("mcp_server_restarted", name=name, tool_count=len(tools))
        return tools
      except Exception as exc:  # noqa: BLE001
        last_error = exc
        delay = self._BACKOFF_BASE_SECONDS * (2 ** attempt)
        logger.warning(
          "mcp_restart_attempt_failed",
          name=name,
          attempt=attempt + 1,
          delay=delay,
          error=str(exc),
        )
        if attempt < attempts:
          await asyncio.sleep(delay)

    error_msg = f"MCP server '{name}' restart exhausted after {attempts + 1} attempts"
    if last_error is not None:
      error_msg += f": {last_error}"

    self._status[name] = MCPStatus(
      name=name,
      status="error",
      transport=config.transport,
      tool_count=0,
      last_ping=None,
      restart_count=restart_count,
      error=str(last_error) if last_error is not None else error_msg,
    )
    self._rebuild_tool_list()
    raise MCPRestartExhaustedError(error_msg)

  async def stop_all(self) -> None:
    """Gracefully stop all MCP servers and the health-check loop."""
    self._stop_event.set()
    if self._health_task is not None:
      try:
        # Give the health loop a chance to finish any in-flight ping
        # before we tear down the clients.
        await asyncio.wait_for(self._health_task, timeout=5.0)
      except asyncio.TimeoutError:
        self._health_task.cancel()
        try:
          await self._health_task
        except asyncio.CancelledError:
          pass
      except asyncio.CancelledError:
        pass
      self._health_task = None

    # Disconnect clients sequentially. The stdio transport teardown can race
    # with anyio cancel scopes; nonoka's MCPClient now swallows those.
    for name in list(self._clients.keys()):
      await self._disconnect_one(name)

    self._clients.clear()
    self._tools.clear()
    logger.info("mcp_servers_stopped")

  async def _disconnect_one(self, name: str) -> None:
    """Disconnect a single client and update its status to stopped."""
    client = self._clients.pop(name, None)
    if client is not None:
      try:
        await client.disconnect()
      except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_disconnect_failed", name=name, error=str(exc))

    status = self._status.get(name)
    if status is not None:
      self._status[name] = MCPStatus(
        name=name,
        status="stopped",
        transport=status.transport,
        tool_count=0,
        last_ping=status.last_ping,
        restart_count=status.restart_count,
        error=status.error,
      )

  # ------------------------------------------------------------------ #
  # Health checks
  # ------------------------------------------------------------------ #

  async def _health_check_loop(self) -> None:
    """Periodically ping all connected servers and restart unhealthy ones."""
    while not self._stop_event.is_set():
      try:
        await asyncio.wait_for(
          self._stop_event.wait(),
          timeout=self._HEALTH_INTERVAL_SECONDS,
        )
      except asyncio.TimeoutError:
        pass

      if self._stop_event.is_set():
        break

      await self._run_health_check()

  async def _run_health_check(self) -> None:
    """Ping each connected server once and restart any that fail."""
    for name, client in list(self._clients.items()):
      try:
        await client.session.send_ping()
        status = self._status[name]
        self._status[name] = MCPStatus(
          name=name,
          status="connected",
          transport=status.transport,
          tool_count=status.tool_count,
          last_ping=datetime.now(),
          restart_count=status.restart_count,
          error=None,
        )
      except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_health_check_failed", name=name, error=str(exc))
        status = self._status[name]
        self._status[name] = MCPStatus(
          name=name,
          status="error",
          transport=status.transport,
          tool_count=0,
          last_ping=status.last_ping,
          restart_count=status.restart_count,
          error=str(exc),
        )
        try:
          await self.restart(name)
        except MCPRestartExhaustedError:
          logger.error("mcp_auto_restart_exhausted", name=name)

  # ------------------------------------------------------------------ #
  # Tool / status access
  # ------------------------------------------------------------------ #

  def get_status(self, name: str) -> MCPStatus:
    """Return the current status for a named server."""
    if name not in self._status:
      raise MCPConnectionError(f"MCP server '{name}' is not known.")
    return self._status[name]

  def list_status(self) -> dict[str, MCPStatus]:
    """Return a snapshot of all server statuses."""
    return dict(self._status)

  def get_tools(self) -> list[Capability]:
    """Return all currently available MCP capabilities."""
    return list(self._tools)

  def _rebuild_tool_list(self) -> None:
    """Rebuild the merged capability list from all connected clients."""
    tools: list[Capability] = []
    for name, client in self._clients.items():
      try:
        tools.extend(client.tools)
      except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_tool_list_failed", name=name, error=str(exc))
    self._tools = tools
