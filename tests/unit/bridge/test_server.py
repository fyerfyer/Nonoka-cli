"""Regression tests for bridge server scheduling and cancellation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nonoka_cli.bridge.server import BridgeServer, _create_stdin_reader


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


class _RecordingHandler:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.shutdown_called = False
        self.started = asyncio.Event()

    async def handle(self, msg: Any) -> None:
        self.messages.append(msg)
        self.started.set()

    async def send(self, _msg: Any) -> None:
        return None

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


@pytest.mark.asyncio
async def test_server_accepts_large_bounded_ndjson_tool_transcript():
    """A resumed OpenCode turn can exceed asyncio's 64 KiB default limit."""
    reader = _create_stdin_reader()
    writer = _Writer()
    handler = _RecordingHandler()
    server = BridgeServer(reader, writer)
    server._handler = handler

    payload = "x" * (70 * 1024)
    reader.feed_data(
        json.dumps({"type": "chat", "messages": [{"role": "user", "content": payload}]}).encode()
        + b"\n"
    )
    task = asyncio.create_task(server.run())
    await asyncio.wait_for(handler.started.wait(), timeout=1)
    reader.feed_eof()

    assert await asyncio.wait_for(task, timeout=1) == 0
    assert handler.shutdown_called is True
    assert handler.messages[0].messages[-1].content == payload
