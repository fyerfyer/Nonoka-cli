"""Shared rich console for nonoka-cli UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from rich.console import Console

_console: Console | None = None


def get_console() -> "Console":
  """Return the singleton rich Console instance.

  Lazily creates a default console on first call so that import-time
  side effects are kept minimal.
  """
  global _console
  if _console is None:
    from rich.console import Console
    _console = Console(highlight=False)
  return _console


def set_console(console: "Console") -> None:
  """Replace the global console instance (useful for tests)."""
  global _console
  _console = console
