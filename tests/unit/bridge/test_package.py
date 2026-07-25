"""Public bridge package exports stay importable with lazy server loading."""

from __future__ import annotations


def test_bridge_server_is_available_from_package():
  from nonoka_cli.bridge import BridgeServer
  from nonoka_cli.bridge.server import BridgeServer as DirectBridgeServer

  assert BridgeServer is DirectBridgeServer
