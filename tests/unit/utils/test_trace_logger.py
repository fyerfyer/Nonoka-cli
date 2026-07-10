"""Tests for TraceLogger."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from nonoka_cli.utils.trace_logger import TraceLogger


def test_trace_logger_writes_ndjson():
  with tempfile.TemporaryDirectory() as tmp:
    logger = TraceLogger(request_id="req-123", trace_dir=tmp)
    logger.log_request(
      session_id="sess-1",
      cwd="/tmp/workspace",
      message_count=3,
      roles=["system", "user", "assistant"],
      tools=["bash"],
    )

    files = list(Path(tmp).glob('trace-*.jsonl'))
    assert len(files) == 1
    lines = files[0].read_text(encoding='utf-8').strip().split('\n')
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['request_id'] == 'req-123'
    assert record['event'] == 'request_entry'
    assert record['session_id'] == 'sess-1'
    assert record['cwd'] == '/tmp/workspace'
    assert record['tools'] == ['bash']


def test_trace_logger_uses_env_var():
  with tempfile.TemporaryDirectory() as tmp:
    os.environ['NONOKA_TRACE_DIR'] = tmp
    try:
      logger = TraceLogger(request_id='req-456')
      logger.log('test_event', foo='bar')
      files = list(Path(tmp).glob('trace-*.jsonl'))
      assert len(files) == 1
      record = json.loads(files[0].read_text(encoding='utf-8').strip())
      assert record['request_id'] == 'req-456'
      assert record['event'] == 'test_event'
      assert record['foo'] == 'bar'
    finally:
      del os.environ['NONOKA_TRACE_DIR']
