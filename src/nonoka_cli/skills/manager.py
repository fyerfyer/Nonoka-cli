"""Skill loading and application manager for nonoka-cli."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from nonoka import Agent, Skill, SkillLoader

logger = structlog.get_logger("nonoka_cli.skills")


@dataclass
class SkillInfo:
  """Human-readable metadata for a discovered skill."""

  name: str
  description: str
  source: str


class SkillManager:
  """Load nonoka skills from standard paths and apply them to Agents.

  Search order (later overrides earlier for duplicate names):
    1. ``./skills/`` — project-level skills
    2. ``./.nonoka/skills/`` — project hidden directory
    3. ``~/.config/nonoka/skills/`` — user-level global skills

  The ``enabled`` list from config can contain either skill names or explicit
  ``*.md`` file paths.
  """

  _STANDARD_PATHS: tuple[Path, ...] = (
    Path("skills"),
    Path(".nonoka/skills"),
    Path.home() / ".config" / "nonoka" / "skills",
  )

  def __init__(self, search_paths: list[Path | str] | None = None):
    """Args:
      search_paths: Additional directories to scan. Standard paths are always
        included unless an empty list is passed; in that case only standard
        paths are used.
    """
    self.search_paths: list[Path] = [
      Path(p).expanduser() for p in (search_paths or [])
    ]
    self._all_paths: list[Path] = list(self._STANDARD_PATHS) + self.search_paths
    self._loaded: list[Skill] = []
    self._available: dict[str, Skill] = {}

  @property
  def standard_paths(self) -> list[Path]:
    """Return the standard skill search paths."""
    return [p.expanduser() for p in self._STANDARD_PATHS]

  def load_all(self, enabled: list[str] | None = None) -> list[Skill]:
    """Load all available skills, optionally filtering to *enabled* names.

    Args:
      enabled: Skill names or explicit ``*.md`` paths to load. If ``None``,
        all discovered skills are loaded.

    Returns:
      The loaded ``Skill`` objects in the order they should be applied.
    """
    self._available = self._discover_all()

    if enabled is None:
      skills = list(self._available.values())
    else:
      skills = []
      for entry in enabled:
        skill = self._resolve_enabled_entry(entry)
        if skill is not None:
          skills.append(skill)
        else:
          logger.warning("skill_not_found", entry=entry)

    self._loaded = skills
    logger.info(
      "skills_loaded",
      available=len(self._available),
      enabled_count=len(skills),
      names=[s.name for s in skills],
    )
    return skills

  def reload(self, enabled: list[str] | None = None) -> list[Skill]:
    """Reload skills from disk."""
    return self.load_all(enabled)

  def apply_to(self, agent: Agent, skill_names: list[str]) -> Agent:
    """Apply named skills to *agent* and return the merged Agent.

    Skills are applied in the order given. Each application returns a new
    Agent, so later skills override earlier ones for conflicting tool names.

    Args:
      agent: The base nonoka Agent.
      skill_names: Names or explicit ``*.md`` paths of skills to apply.

    Returns:
      A new Agent with skills merged in.
    """
    skills = self.load_all(skill_names)
    merged = agent
    for skill in skills:
      merged = skill.apply_to(merged)
      logger.debug("skill_applied", name=skill.name)
    return merged

  def list_available(self) -> list[SkillInfo]:
    """List all discovered skills (including unloaded ones)."""
    if not self._available:
      self._available = self._discover_all()
    return [
      SkillInfo(
        name=skill.name,
        description=skill.description,
        source=getattr(skill, "_source", "<unknown>"),
      )
      for skill in self._available.values()
    ]

  def list_loaded(self) -> list[SkillInfo]:
    """List currently loaded skills."""
    return [
      SkillInfo(
        name=skill.name,
        description=skill.description,
        source=getattr(skill, "_source", "<unknown>"),
      )
      for skill in self._loaded
    ]

  def _discover_all(self) -> dict[str, Skill]:
    """Discover all ``*.md`` skill files across search paths."""
    skills_by_name: dict[str, Skill] = {}

    for path in self._all_paths:
      path = path.expanduser()
      if not path.exists() or not path.is_dir():
        continue
      for skill in SkillLoader(path).load_all():
        # Attach source metadata for debugging / listing.
        object.__setattr__(skill, "_source", str(path / f"{skill.name}.md"))
        if skill.name in skills_by_name:
          logger.warning(
            "skill_duplicate",
            name=skill.name,
            path=str(path),
          )
        skills_by_name[skill.name] = skill

    return skills_by_name

  def _resolve_enabled_entry(self, entry: str) -> Skill | None:
    """Resolve a single enabled skill entry to a ``Skill`` instance."""
    entry = entry.strip()
    if not entry:
      return None

    # Explicit file path.
    if entry.endswith(".md") or "/" in entry or "\\" in entry:
      path = Path(entry).expanduser()
      if path.exists():
        try:
          skill = SkillLoader.load_file(path)
          object.__setattr__(skill, "_source", str(path))
          return skill
        except Exception as exc:  # noqa: BLE001
          logger.warning("skill_load_failed", path=str(path), error=str(exc))
          return None
      logger.warning("skill_file_not_found", path=str(path))
      return None

    # Name lookup in discovered skills.
    if entry in self._available:
      return self._available[entry]

    # Try standard path search as a fallback.
    for path in self._all_paths:
      candidate = path.expanduser() / f"{entry}.md"
      if candidate.exists():
        try:
          skill = SkillLoader.load_file(candidate)
          object.__setattr__(skill, "_source", str(candidate))
          self._available[skill.name] = skill
          return skill
        except Exception as exc:  # noqa: BLE001
          logger.warning("skill_load_failed", path=str(candidate), error=str(exc))

    return None

  def get_skill(self, name: str) -> Skill | None:
    """Get a loaded or available skill by name."""
    for skill in self._loaded:
      if skill.name == name:
        return skill
    if not self._available:
      self._available = self._discover_all()
    return self._available.get(name)

  def __repr__(self) -> str:
    return f"<SkillManager loaded={len(self._loaded)} available={len(self._available)}>"
