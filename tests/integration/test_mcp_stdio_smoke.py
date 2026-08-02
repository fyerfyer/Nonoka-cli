"""Opt-in live smoke test for the CLI's real stdio MCP lifecycle."""

from __future__ import annotations

import os

import pytest

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.mcp.manager import MCPManager


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    os.getenv("NONOKA_MCP_SMOKE") != "1",
    reason="set NONOKA_MCP_SMOKE=1 to download and start the official MCP sample server",
  ),
]


async def test_stdio_mcp_manager_connects_to_official_everything_server() -> None:
  manager = MCPManager()
  try:
    tools = await manager.start_all(
      {
        "everything": MCPServerConfigModel(
          transport="stdio",
          command="npx",
          args=["-y", "@modelcontextprotocol/server-everything"],
          startup_timeout_seconds=120,
        )
      }
    )
    assert tools
  finally:
    await manager.stop_all()
