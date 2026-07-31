"""One-tool stdio MCP server for the interview demo.

It intentionally uses the small JSON-RPC surface directly so the demo does not
depend on a second server framework or network download. Nonoka still launches
it as a real child process, performs the MCP handshake, discovers its tool, and
invokes it through the normal MCP client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SERVER_INFO = {"name": "parcelwatch-product-contract", "version": "1.0.0"}
TOOL_NAME = "get_reconciliation_contract"


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
  return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _handle(message: dict[str, Any], contract_path: Path) -> dict[str, Any] | None:
  request_id = message.get("id")
  method = message.get("method")
  params = message.get("params") or {}

  if method == "initialize":
    return _response(
      request_id,
      {
        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
      },
    )
  if method in {"notifications/initialized", "notifications/cancelled"}:
    return None
  if method == "ping":
    return _response(request_id, {})
  if method == "tools/list":
    return _response(
      request_id,
      {
        "tools": [
          {
            "name": TOOL_NAME,
            "description": "Return the authoritative ParcelWatch carrier-feed contract.",
            "inputSchema": {
              "type": "object",
              "properties": {
                "component": {
                  "type": "string",
                  "description": "Contract component; use carrier-feed.",
                }
              },
              "required": ["component"],
              "additionalProperties": False,
            },
          }
        ]
      },
    )
  if method == "tools/call":
    arguments = params.get("arguments") or {}
    if params.get("name") != TOOL_NAME or arguments.get("component") != "carrier-feed":
      return _response(
        request_id,
        {
          "content": [{"type": "text", "text": "Unknown contract component."}],
          "isError": True,
        },
      )
    return _response(
      request_id,
      {
        "content": [{"type": "text", "text": contract_path.read_text(encoding="utf-8")}],
        "isError": False,
      },
    )
  if request_id is None:
    return None
  return {
    "jsonrpc": "2.0",
    "id": request_id,
    "error": {"code": -32601, "message": f"Method not found: {method}"},
  }


def main() -> None:
  contract_path = Path(sys.argv[1]).resolve()
  for raw in sys.stdin:
    try:
      message = json.loads(raw)
      response = _handle(message, contract_path)
    except Exception as exc:  # keep protocol errors on the protocol channel
      response = {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32603, "message": str(exc)},
      }
    if response is not None:
      sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
      sys.stdout.flush()


if __name__ == "__main__":
  main()
