"""History implementations for nonoka-cli."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from prompt_toolkit.history import FileHistory


class TrimmingFileHistory(FileHistory):
  """FileHistory that keeps only the most recent ``max_entries`` lines.

  Prompt-toolkit's built-in ``FileHistory`` appends indefinitely. This
  subclass trims the backing file on load so the history file does not grow
  without bound.
  """

  def __init__(self, filename: str, max_entries: int = 1000):
    """Args:
      filename: Path to the history file.
      max_entries: Maximum number of history entries to retain.
    """
    self._max_entries = max(max_entries, 1)
    super().__init__(filename)

  def load_history_strings(self) -> Iterable[str]:
    """Yield history strings, trimming the file if it has grown too large."""
    # Load all existing lines via the parent implementation.
    lines = list(super().load_history_strings())

    if len(lines) > self._max_entries:
      lines = lines[-self._max_entries:]
      self._rewrite_history(lines)

    return lines

  def _rewrite_history(self, lines: list[str]) -> None:
    """Rewrite the backing file to contain only ``lines``."""
    try:
      path = Path(self.filename)
      path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    except OSError:
      # If trimming fails, continue with the in-memory list.
      pass
