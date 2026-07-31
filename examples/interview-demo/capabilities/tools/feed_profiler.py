from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from nonoka import tool


@tool
def profile_feed(path: str) -> dict:
  """Profile a carrier JSONL feed without changing it.

  Returns bounded structural evidence: line count, malformed line numbers,
  duplicate event IDs, and observed timezone suffixes.
  """
  source = Path(path)
  ids: list[str] = []
  invalid_lines: list[int] = []
  timezone_suffixes: set[str] = set()
  for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
    try:
      item = json.loads(raw)
    except json.JSONDecodeError:
      invalid_lines.append(line_number)
      continue
    event_id = item.get("event_id")
    if isinstance(event_id, str):
      ids.append(event_id)
    occurred_at = item.get("occurred_at")
    if isinstance(occurred_at, str):
      if occurred_at.endswith("Z"):
        timezone_suffixes.add("Z")
      elif len(occurred_at) >= 6 and occurred_at[-6] in {"+", "-"}:
        timezone_suffixes.add(occurred_at[-6:])
  counts = Counter(ids)
  return {
    "line_count": line_number if "line_number" in locals() else 0,
    "invalid_lines": invalid_lines,
    "duplicate_event_ids": sorted(key for key, value in counts.items() if value > 1),
    "timezone_suffixes": sorted(timezone_suffixes),
  }
