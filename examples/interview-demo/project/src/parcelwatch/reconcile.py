from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _parse_timestamp(value: str) -> datetime:
  # BUG: returning the raw string's local wall time makes offset timestamps
  # compare incorrectly after callers discard timezone information.
  return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def reconcile_feed(path: str | Path) -> dict[str, Any]:
  """Reconcile carrier events into package states.

  This initial implementation is intentionally incomplete for the interview
  exercise: it loses input coordinates, keeps the last duplicate, and sorts
  offset timestamps incorrectly.
  """
  source = Path(path)
  by_event_id: dict[str, dict[str, Any]] = {}
  rejected: list[dict[str, Any]] = []
  for raw in source.read_text(encoding="utf-8").splitlines():
    if not raw.strip():
      continue
    try:
      event = json.loads(raw)
    except json.JSONDecodeError:
      rejected.append({"reason": "malformed_json"})
      continue
    by_event_id[event["event_id"]] = event

  ordered = sorted(by_event_id.values(), key=lambda event: _parse_timestamp(event["occurred_at"]))
  states: dict[str, str] = {}
  accepted: list[str] = []
  for event in ordered:
    states[event["package_id"]] = event["status"]
    accepted.append(event["event_id"])
  return {"accepted": accepted, "rejected": rejected, "states": states}
