from __future__ import annotations

from pathlib import Path

from nonoka import tool


@tool
def count_nonempty_lines(path: str) -> dict[str, int]:
  """Count non-empty UTF-8 text lines without modifying the file."""
  source = Path(path)
  return {"non_empty_lines": sum(bool(line.strip()) for line in source.read_text().splitlines())}
