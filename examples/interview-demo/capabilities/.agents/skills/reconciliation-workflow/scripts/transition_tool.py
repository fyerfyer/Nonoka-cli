from __future__ import annotations

from nonoka import tool


@tool
def check_transition(previous: str | None, proposed: str) -> dict:
  """Check one ParcelWatch package-state transition against the domain graph."""
  allowed = {None: "CREATED", "CREATED": "IN_TRANSIT", "IN_TRANSIT": "DELIVERED"}
  expected = allowed.get(previous)
  return {
    "allowed": proposed == expected,
    "previous": previous,
    "proposed": proposed,
    "expected": expected,
  }
