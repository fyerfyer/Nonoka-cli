"""Human-readable session index commands."""

from __future__ import annotations

import argparse
import asyncio
import json

from nonoka.observability import SQLiteEventStore

from nonoka_cli.commands.logs_cmd import _event_db
from nonoka_cli.sessions.manager import SessionManager


def run_sessions(args: argparse.Namespace) -> int:
  async def execute():
    async with SessionManager() as manager:
      if args.sessions_command == "list":
        return await manager.list()
      item = await manager.get(args.session_id)
      if item is None:
        return None, [], None
      store = SQLiteEventStore(_event_db())
      try:
        return item, await store.list(args.session_id, 20), await store.summary(args.session_id)
      finally:
        await store.close()
  result = asyncio.run(execute())
  if args.sessions_command == "list":
    for item in result:
      print(f"{item.session_id}\t{item.model}\t{item.last_active.isoformat()}\t{item.name or ''}")
    return 0
  item, events, usage = result
  if item is None:
    print(f"Session not found: {args.session_id}")
    return 1
  payload = {
    "session_id": item.session_id,
    "name": item.name,
    "model": item.model,
    "created_at": item.created_at,
    "last_active": item.last_active,
    "message_count": item.message_count,
    "usage": usage.__dict__,
    "events": events,
  }
  print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
  return 0


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
  parser = subparsers.add_parser("sessions", help="List and inspect sessions")
  children = parser.add_subparsers(dest="sessions_command", required=True)
  list_parser = children.add_parser("list", help="List sessions")
  list_parser.set_defaults(func=run_sessions)
  show_parser = children.add_parser("show", help="Show a session timeline")
  show_parser.add_argument("session_id")
  show_parser.set_defaults(func=run_sessions)
