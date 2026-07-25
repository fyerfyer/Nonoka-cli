"""Query persisted structured execution events."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from nonoka.observability import SQLiteEventStore


def _event_db() -> Path:
  default = Path.home() / ".local" / "share" / "nonoka" / "events.db"
  return Path(os.getenv("NONOKA_EVENT_DB", str(default)))


def run_logs(args: argparse.Namespace) -> int:
  async def query() -> list[dict]:
    store = SQLiteEventStore(_event_db())
    try:
      return await store.list(args.session_id, args.limit)
    finally:
      await store.close()
  events = asyncio.run(query())
  if args.json:
    print(json.dumps(events, ensure_ascii=False, default=str))
  else:
    for event in events:
      payload = json.dumps(event["payload"], ensure_ascii=False, default=str)
      print(
        f"{event['occurred_at']} {event['session_id']} "
        f"{event['event_type']} {payload}"
      )
  return 0


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
  parser = subparsers.add_parser("logs", help="Show structured execution events")
  parser.add_argument("--session-id")
  parser.add_argument("--limit", type=int, default=100)
  parser.add_argument("--json", action="store_true")
  parser.set_defaults(func=run_logs)
