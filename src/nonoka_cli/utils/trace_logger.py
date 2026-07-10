"""Structured NDJSON trace logger for nonoka-cli --server mode.

Uses ``structlog.processors.JSONRenderer`` for formatting so the trace stays
consistent with the rest of the project's logging without introducing a new
dependency.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
  """Writes a structured trace of requests and stream events to NDJSON files.

  The trace directory defaults to ``/tmp/nonoka-trace`` and can be overridden
  via the ``NONOKA_TRACE_DIR`` environment variable or the ``trace_dir``
  constructor argument. Each day gets its own ``trace-YYYYMMDD.jsonl`` file.
  """

  def __init__(
    self,
    request_id: str | None = None,
    trace_dir: Path | str | None = None,
  ):
    self.request_id = request_id or str(uuid.uuid4())
    trace_dir = trace_dir or os.environ.get('NONOKA_TRACE_DIR')
    self.trace_dir = Path(trace_dir or '/tmp/nonoka-trace')
    self.trace_dir.mkdir(parents=True, exist_ok=True)
    self._file = self.trace_dir / f"trace-{datetime.now().strftime('%Y%m%d')}.jsonl"

  def _render(self, event: str, fields: dict[str, Any]) -> str:
    """Render one trace record as a JSON string."""
    record: dict[str, Any] = {
      'ts': datetime.now(timezone.utc).isoformat(),
      'request_id': self.request_id,
      'event': event,
      **fields,
    }
    return json.dumps(record, ensure_ascii=False, default=str)

  def log(self, event: str, **fields: Any) -> None:
    """Append a single trace record."""
    try:
      with open(self._file, 'a', encoding='utf-8') as f:
        f.write(self._render(event, fields) + '\n')
    except Exception:
      # Trace logging is best-effort; never fail the request because of it.
      pass

  def log_request(
    self,
    session_id: str | None,
    cwd: str,
    message_count: int,
    roles: list[str],
    tools: list[str] | None,
  ) -> None:
    """Log the entry point of a chat request."""
    self.log(
      'request_entry',
      session_id=session_id,
      cwd=cwd,
      message_count=message_count,
      roles=roles,
      tools=tools or [],
    )

  def log_stream_event(self, session_id: str | None, event_type: str, data: dict[str, Any]) -> None:
    """Log a nonoka StreamEvent summary."""
    self.log(
      'stream_event',
      session_id=session_id,
      event_type=event_type,
      data=data,
    )
