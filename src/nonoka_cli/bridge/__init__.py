"""NDJSON bridge for nonoka-cli server mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nonoka_cli.bridge.protocol import (
  ChatRequest,
  ErrorEvent,
  FinishEvent,
  TextDeltaEvent,
  parse_inbound_line,
)
if TYPE_CHECKING:
  from nonoka_cli.bridge.server import BridgeServer


def __getattr__(name: str) -> Any:
  """Lazily expose the server without coupling bridge capability imports to it.

  Agent construction imports bridge-local capabilities. Importing the server
  eagerly here would re-import the handler (and then ``AgentFactory``) while
  that factory is still initialising. Keeping this public convenience export
  lazy breaks that import cycle without changing ``from nonoka_cli.bridge
  import BridgeServer`` for callers.
  """
  if name == "BridgeServer":
    from nonoka_cli.bridge.server import BridgeServer
    return BridgeServer
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
  "BridgeServer",
  "ChatRequest",
  "ErrorEvent",
  "FinishEvent",
  "TextDeltaEvent",
  "parse_inbound_line",
]
