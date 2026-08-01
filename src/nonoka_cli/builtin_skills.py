"""Built-in configuration skills shipped with nonoka-cli."""

from __future__ import annotations

from pathlib import Path

BUILTIN_SKILL_NAMES = (
  "skill-creator",
  "mcp-creator",
  "config-editor",
  "subagent-creator",
)


def bundled_skills_path() -> Path:
  """Return the installed directory containing Nonoka's built-in skills."""
  return Path(__file__).resolve().parent / "skills"


def enabled_skill_names(configured: list[str]) -> list[str]:
  """Combine shipped skills with configured skills without duplicates."""
  return list(dict.fromkeys([*BUILTIN_SKILL_NAMES, *configured]))
