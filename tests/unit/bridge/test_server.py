"""Regression tests for bridge server scheduling and cancellation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nonoka_cli.bridge.server import BridgeServer


class _Writer:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.lines.append(json.loads(data.decode("utf-8")))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class _BlockingHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.shutdown_called = False

    async def handle(self, _msg: Any) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def send(self, msg: Any) -> None:
        self._writer.write((msg.model_dump_json(exclude_none=True) + "\n").encode("utf-8"))

    async def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_cancel_interrupts_active_turn_and_emits_finish_event():
    reader = asyncio.StreamReader()
    writer = _Writer()
    handler = _BlockingHandler()
    handler._writer = writer
    server = BridgeServer(reader, writer)
    server._handler = handler

    task = asyncio.create_task(server.run())
    reader.feed_data(b'{"type":"chat","messages":[{"role":"user","content":"wait"}]}\n')
    await asyncio.wait_for(handler.started.wait(), timeout=1)
    reader.feed_data(b'{"type":"cancel"}\n')
    reader.feed_eof()
    assert await asyncio.wait_for(task, timeout=1) == 0

    assert handler.cancelled is True
    assert handler.shutdown_called is True
    assert {"type": "finish", "finish_reason": "cancel"} in writer.lines
