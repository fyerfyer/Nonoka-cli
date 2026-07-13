"""Convert ``.nonoka/plugin.json`` manifests to OpenCode-compatible artifacts.

OpenCode loads skills natively from ``.opencode/skills/<name>/SKILL.md`` and
applies permissions via ``opencode.json``. Agents, commands, hooks, and MCP
servers are not representable as static files in OpenCode; they require a
code-first plugin module. This converter therefore focuses on skills and the
permission snippet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonoka_cli.core.plugin_manifest import PluginManifest


def _skill_to_markdown(skill: Any) -> str:
  """Render a nonoka skill entry as an OpenCode-compatible SKILL.md."""
  lines = [
    "---",
    f"name: {skill.name}",
    f"description: {skill.description or 'No description'}",
    "---",
    "",
    skill.activation_prompt or skill.system_prompt or skill.description or "",
  ]
  return "\n".join(lines).rstrip() + "\n"


def convert_to_opencode(manifest: PluginManifest) -> dict[str, Any]:
  """Return an OpenCode config snippet for *manifest*.

  The returned dict is intended to be merged into ``opencode.json``. It sets
  skill permissions so that declared allowed tools/skills do not require a
  prompt. OpenCode manages agents, MCP servers, commands, and hooks through
  its own plugin/commands layer, so those manifest sections are not converted
  to static JSON here.
  """
  output: dict[str, Any] = {}
  permission: dict[str, Any] = {}

  if manifest.skills:
    permission["skill"] = {"*": "allow"}

  for tool in manifest.allowed_tools:
    permission[tool] = "allow"

  if permission:
    output["permission"] = permission

  return output


def write_opencode_files(
  manifest: PluginManifest,
  output_dir: Path | str,
) -> list[Path]:
  """Write OpenCode-compatible skill files under ``.opencode/skills/``.

  Returns the list of written paths.
  """
  output_dir = Path(output_dir)
  written: list[Path] = []
  for skill in manifest.skills:
    skill_dir = output_dir / ".opencode" / "skills" / skill.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_skill_to_markdown(skill), encoding="utf-8")
    written.append(skill_file)
  return written
