"""Tests for bounded MCP startup behaviour."""

import asyncio

import pytest

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.mcp.manager import MCPManager
from nonoka_cli.utils.errors import MCPRestartExhaustedError


class _SlowManager:
  def __init__(self) -> None:
    self.stopped = False

  async def start_all(self, _configs):
    await asyncio.sleep(0.05)
    return []

  async def stop_all(self) -> None:
    self.stopped = True

  def list_status(self):
    return {}


async def test_start_all_bounds_a_slow_mcp_process(monkeypatch) -> None:
  manager = MCPManager()
  slow = _SlowManager()
  manager._inner = slow  # type: ignore[assignment]

  async def timeout_immediately(awaitable, *, timeout):
    awaitable.close()
    raise asyncio.TimeoutError

  monkeypatch.setattr("nonoka_cli.mcp.manager.asyncio.wait_for", timeout_immediately)

  with pytest.raises(MCPRestartExhaustedError, match="timed out after 1s"):
    await manager.start_all(
      {
        "slow": MCPServerConfigModel(
          transport="stdio",
          command="slow-server",
          startup_timeout_seconds=1,
        )
      }
    )

  assert slow.stopped is True
  status = manager.list_status()["slow"]
  assert status.status == "error"
  assert status.tool_count == 0
  assert status.error == "MCP startup timed out after 1s; check network/package logs."


async def test_start_all_returns_without_touching_manager_when_no_servers() -> None:
  manager = MCPManager()
  assert await manager.start_all({}) == []


def test_stdio_config_forwards_only_required_srt_runtime_environment(monkeypatch) -> None:
  from nonoka_cli.mcp.manager import _to_agent_config

  monkeypatch.setenv("NPM_CONFIG_CACHE", "/workspace/.nonoka/npm-cache")
  monkeypatch.setenv("HTTPS_PROXY", "http://sandbox-proxy.invalid:4444")
  monkeypatch.setenv("SSL_CERT_FILE", "/tmp/srt-ca.pem")
  monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-forwarded")
  monkeypatch.setenv("UNRELATED_VALUE", "must-not-be-forwarded")

  config = _to_agent_config(
    MCPServerConfigModel(transport="stdio", command="npx")
  )

  assert {
    "NPM_CONFIG_CACHE": "/workspace/.nonoka/npm-cache",
    "HTTPS_PROXY": "http://sandbox-proxy.invalid:4444",
    "SSL_CERT_FILE": "/tmp/srt-ca.pem",
  }.items() <= (config.env or {}).items()
  assert "OPENAI_API_KEY" not in (config.env or {})
  assert "UNRELATED_VALUE" not in (config.env or {})
