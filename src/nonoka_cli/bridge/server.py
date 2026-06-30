"""NDJSON bridge server for nonoka-cli --server mode.

Reads typed messages from stdin, dispatches them to a ``ChatRequestHandler``,
and writes typed NDJSON messages back to stdout. All plain logs go to stderr
so that stdout remains a clean protocol channel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import structlog

from nonoka_cli.bridge.handler import ChatRequestHandler, build_sender
from nonoka_cli.bridge.protocol import (
  ApprovalResponse,
  ChatRequest,
  ErrorEvent,
  parse_inbound_line,
)
from nonoka_cli.utils.logging import setup_logging

logger = structlog.get_logger("nonoka_cli.bridge")


class BridgeServer:
  """Long-lived NDJSON server wrapping a single ``ChatRequestHandler``."""

  def __init__(
    self,
    stdin: asyncio.StreamReader,
    stdout: asyncio.StreamWriter,
    config_path: Path | str | None = None,
    model: str | None = None,
  ):
    """Args:
      stdin: Async source of NDJSON lines.
      stdout: Async sink for NDJSON lines.
      config_path: Optional explicit path to the nonoka config file.
      model: Optional model override.
    """
    self._stdin = stdin
    self._stdout = stdout
    self._handler = ChatRequestHandler(
      send=build_sender(stdout),
      config_path=config_path,
      model=model,
    )
    self._running = True

  # ------------------------------------------------------------------ #
  # Public API
  # ------------------------------------------------------------------ #

  async def run(self) -> int:
    """Run the server until stdin closes."""
    logger.info("bridge_server_started")

    try:
      while self._running:
        line = await self._stdin.readline()
        if not line:
          break

        try:
          msg = parse_inbound_line(line.decode("utf-8", errors="replace"))
        except Exception as exc:
          logger.warning("invalid_inbound_message", error=str(exc), line=line.strip())
          await self._handler.send(ErrorEvent(message=f"Invalid message: {exc}"))
          continue

        if msg is None:
          continue

        try:
          if isinstance(msg, ChatRequest):
            await self._handler.handle(msg)
          elif isinstance(msg, ApprovalResponse):
            await self._handler.handle_approval(msg)
          else:
            logger.warning("unexpected_inbound_type", type=type(msg).__name__)
        except Exception as exc:
          logger.error("message_handler_failed", error=str(exc), type=type(msg).__name__)
          await self._handler.send(ErrorEvent(message=f"Handler failed: {exc}"))
    finally:
      await self._shutdown()

    return 0

  # ------------------------------------------------------------------ #
  # Lifecycle
  # ------------------------------------------------------------------ #

  async def _shutdown(self) -> None:
    """Shutdown the handler and close the stdout writer."""
    await self._handler.shutdown()

    try:
      self._stdout.close()
      await self._stdout.wait_closed()
    except Exception:
      pass

    logger.info("bridge_server_stopped")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _setup_logging() -> None:
  """Keep stdout clean for NDJSON; logs go to stderr."""
  level_name = os.environ.get("NONOKA_LOG_LEVEL", "WARNING")
  level = getattr(logging, level_name.upper(), logging.WARNING)
  setup_logging(level=level, console=True)


async def _async_main(
  config_path: Path | str | None = None,
  model: str | None = None,
) -> int:
  """Set up async stdin/stdout and run the bridge server."""
  loop = asyncio.get_event_loop()
  stdin = asyncio.StreamReader()
  await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

  w_transport, w_protocol = await loop.connect_write_pipe(
    asyncio.streams.FlowControlMixin,
    os.fdopen(sys.stdout.fileno(), "wb", closefd=False),
  )
  stdout = asyncio.StreamWriter(w_transport, w_protocol, None, loop)

  server = BridgeServer(stdin, stdout, config_path=config_path, model=model)
  return await server.run()


def main(
  config_path: Path | str | None = None,
  model: str | None = None,
) -> int:
  """Sync entry point for ``nonoka-cli --server``."""
  _setup_logging()
  try:
    return asyncio.run(_async_main(config_path=config_path, model=model))
  except KeyboardInterrupt:
    return 130
  except Exception as exc:
    # stdout is the protocol channel; use stderr for crashes.
    print(f"Bridge server crashed: {exc}", file=sys.stderr)
    logger.error("bridge_server_crashed", error=str(exc))
    return 1
