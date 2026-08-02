"""Declarative request-to-skill routing for lazy-loaded skills."""

from __future__ import annotations

from typing import Any

from nonoka import SkillRegistry


def required_skill_names(prompt: str, registry: SkillRegistry | None) -> list[str]:
  """Return required skills whose own activation metadata matches *prompt*.

  Skills opt in with frontmatter such as::

      metadata:
        activation:
          mode: required
          triggers: ["mcp", "Context7"]

  The router intentionally knows nothing about individual skill names or
  domains. It only turns the skill author's declarative metadata into a
  per-request instruction to use the existing ``load_skill`` tool.
  """
  if registry is None or not prompt.strip():
    return []

  normalized_prompt = prompt.casefold()
  matches: list[str] = []
  for info in registry.enabled:
    skill = registry.get_skill(info.name)
    metadata: dict[str, Any] = getattr(skill, "metadata", {}) if skill else {}
    activation = metadata.get("activation") if isinstance(metadata, dict) else None
    if not isinstance(activation, dict) or str(activation.get("mode", "")).casefold() != "required":
      continue
    triggers = activation.get("triggers", [])
    if not isinstance(triggers, list):
      continue
    if any(
      isinstance(trigger, str)
      and trigger.strip()
      and trigger.casefold() in normalized_prompt
      for trigger in triggers
    ):
      matches.append(info.name)
  return matches
