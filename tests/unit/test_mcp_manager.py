"""Tests for MCP manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.utils.errors import MCPConnectionError, MCPRestartExhaustedError


@pytest.fixture
def manager():
  return MCPManager()


@pytest.fixture
def sample_config():
  return {
    "fetch": MCPServerConfigModel(transport="stdio", command="uvx", args=["mcp-server-fetch"]),
  }


class TestMCPManagerStartAll:
  """Tests for MCPManager.start_all()."""

  @pytest.mark.asyncio
  async def test_start_all_discovers_tools(self, manager, sample_config):
    mock_cap = MagicMock()
    mock_cap.name = "fetch_url"

    with patch("nonoka_cli.mcp.manager.MCPClient") as mock_client_cls:
      client = AsyncMock()
      client.connect = AsyncMock()
      client.get_capabilities = AsyncMock(return_value=[mock_cap])
      client.tools = [mock_cap]
      mock_client_cls.return_value = client

      tools = await manager.start_all(sample_config)

    assert len(tools) == 1
    assert tools[0].name == "fetch_url"
    status = manager.get_status("fetch")
    assert status.status == "connected"
    assert status.tool_count == 1

  @pytest.mark.asyncio
  async def test_start_all_partial_failure_raises_but_keeps_healthy(self, manager):
    configs = {
      "good": MCPServerConfigModel(transport="stdio", command="echo"),
      "bad": MCPServerConfigModel(transport="stdio", command="false"),
    }

    mock_cap = MagicMock()
    mock_cap.name = "good_tool"

    def make_client(*, transport, command, args):
      client = AsyncMock()
      client.connect = AsyncMock()
      if command == "false":
        client.connect.side_effect = RuntimeError("spawn failed")
      client.get_capabilities = AsyncMock(return_value=[mock_cap] if command == "echo" else [])
      client.tools = [mock_cap] if command == "echo" else []
      return client

    with patch("nonoka_cli.mcp.manager.MCPClient") as mock_client_cls:
      mock_client_cls.side_effect = make_client

      with pytest.raises(MCPRestartExhaustedError):
        await manager.start_all(configs)

    assert manager.get_status("good").status == "connected"
    assert manager.get_status("bad").status == "error"
    assert len(manager.get_tools()) == 1


class TestMCPManagerStartServer:
  """Tests for MCPManager.start_server()."""

  @pytest.mark.asyncio
  async def test_start_server_adds_to_pool(self, manager):
    mock_cap = MagicMock()
    mock_cap.name = "new_tool"

    with patch("nonoka_cli.mcp.manager.MCPClient") as mock_client_cls:
      client = AsyncMock()
      client.connect = AsyncMock()
      client.get_capabilities = AsyncMock(return_value=[mock_cap])
      client.tools = [mock_cap]
      mock_client_cls.return_value = client

      config = MCPServerConfigModel(transport="stdio", command="npx", args=["-y", "some-server"])
      tools = await manager.start_server("new", config)

    assert len(tools) == 1
    assert manager.get_status("new").status == "connected"
    assert len(manager.get_tools()) == 1
    assert "new" in manager.list_status()

class TestMCPManagerRestart:
  """Tests for MCPManager.restart()."""

  @pytest.mark.asyncio
  async def test_restart_unknown_server_raises(self, manager):
    with pytest.raises(MCPConnectionError):
      await manager.restart("missing")

  @pytest.mark.asyncio
  async def test_restart_exhausted_raises(self, manager, sample_config):
    with patch("nonoka_cli.mcp.manager.MCPClient") as mock_client_cls:
      client = AsyncMock()
      client.connect = AsyncMock(side_effect=RuntimeError("always fails"))
      client.disconnect = AsyncMock()
      mock_client_cls.return_value = client

      with pytest.raises(MCPRestartExhaustedError):
        await manager.start_all(sample_config)

      # After startup failure, restart should also exhaust quickly.
      with pytest.raises(MCPRestartExhaustedError):
        await manager.restart("fetch")


class TestMCPManagerStatus:
  """Tests for status accessors."""

  @pytest.mark.asyncio
  async def test_list_status_empty(self, manager):
    assert manager.list_status() == {}

  @pytest.mark.asyncio
  async def test_get_status_unknown_raises(self, manager):
    with pytest.raises(MCPConnectionError):
      manager.get_status("missing")


class TestMCPManagerShutdown:
  """Tests for MCPManager.stop_all()."""

  @pytest.mark.asyncio
  async def test_stop_all_disconnects_clients(self, manager, sample_config):
    with patch("nonoka_cli.mcp.manager.MCPClient") as mock_client_cls:
      client = AsyncMock()
      client.connect = AsyncMock()
      client.get_capabilities = AsyncMock(return_value=[])
      client.tools = []
      mock_client_cls.return_value = client

      await manager.start_all(sample_config)
      await manager.stop_all()

      client.disconnect.assert_awaited_once()
      assert manager.list_status()["fetch"].status == "stopped"
      assert manager.get_tools() == []
