"""Plugin manifest loader for nonoka-cli.

Supports ``.nonoka/plugin.json`` files that declare skills, agents, MCP servers,
commands, hooks, and allowed tools. The schema is intentionally close to formats
used by Claude Code, Continue, and OpenCode so manifests can be converted
between ecosystems.
"""

from __future__ import annotations

import json
import structlog
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.utils.errors import ConfigError

logger = structlog.get_logger("nonoka_cli.core")

DEFAULT_MANIFEST_PATHS = [
  Path(".nonoka") / "plugin.json",
]


class SkillEntry(BaseModel):
  """A skill referenced by a plugin manifest."""

  name: str
  description: str = ""
  path: str | None = None
  system_prompt: str = ""
  activation_prompt: str = ""


class AgentEntry(BaseModel):
  """An agent role referenced by a plugin manifest."""

  name: str
  description: str = ""
  model: str = ""
  system_prompt: str = ""
  max_turns: int = 5
  allowed_tools: list[str] = Field(default_factory=list)


class CommandEntry(BaseModel):
  """A custom CLI command declared by a plugin manifest."""

  name: str
  description: str = ""
  command: str


class HookEntry(BaseModel):
  """A lifecycle hook declared by a plugin manifest."""

  event: str
  command: str


class PluginManifest(BaseModel):
  """Schema for ``.nonoka/plugin.json``.

  The manifest is intentionally minimal and omits implementation details so
  that it can be transformed to/from Claude Code / Continue / OpenCode plugin
  descriptors.
  """

  schema_version: str = "1.0"
  name: str = ""
  description: str = ""
  skills: list[SkillEntry] = Field(default_factory=list)
  agents: list[AgentEntry] = Field(default_factory=list)
  mcp_servers: dict[str, MCPServerConfigModel] = Field(default_factory=dict)
  commands: list[CommandEntry] = Field(default_factory=list)
  hooks: list[HookEntry] = Field(default_factory=list)
  allowed_tools: list[str] = Field(default_factory=list)

  @field_validator("mcp_servers", mode="before")
  @classmethod
  def _validate_mcp_servers(cls, v: Any) -> dict[str, Any]:
    if v is None:
      return {}
    if not isinstance(v, dict):
      raise ValueError("mcp_servers must be a dict")
    return v


class PluginManifestLoader:
  """Discovers and loads plugin manifests from a working directory."""

  def __init__(self, extra_paths: list[Path] | None = None):
    self._extra_paths = [Path(p).expanduser() for p in (extra_paths or [])]

  def discover(self, working_dir: Path) -> list[Path]:
    """Return all existing manifest paths for *working_dir*."""
    candidates = [
      working_dir / p for p in DEFAULT_MANIFEST_PATHS
    ] + [working_dir / p for p in self._extra_paths]
    return [p for p in candidates if p.exists() and p.is_file()]

  def load(self, working_dir: Path) -> list[PluginManifest]:
    """Load all manifests discovered in *working_dir*."""
    manifests: list[PluginManifest] = []
    for path in self.discover(working_dir):
      try:
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = PluginManifest.model_validate(data)
        manifests.append(manifest)
        logger.info("plugin_manifest_loaded", path=str(path), name=manifest.name)
      except json.JSONDecodeError as exc:
        logger.warning("plugin_manifest_invalid_json", path=str(path), error=str(exc))
      except Exception as exc:
        logger.warning("plugin_manifest_load_failed", path=str(path), error=str(exc))
    return manifests


def merge_manifests(manifests: list[PluginManifest]) -> PluginManifest:
  """Merge multiple manifests into a single effective manifest.

  Later manifests override earlier ones by name for keyed collections
  (agents, mcp_servers, commands). Lists are concatenated.
  """
  merged = PluginManifest()
  for manifest in manifests:
    merged.skills.extend(manifest.skills)
    merged.commands.extend(manifest.commands)
    merged.hooks.extend(manifest.hooks)
    for tool in manifest.allowed_tools:
      if tool not in merged.allowed_tools:
        merged.allowed_tools.append(tool)

    seen_agents = {a.name: idx for idx, a in enumerate(merged.agents)}
    for agent in manifest.agents:
      if agent.name in seen_agents:
        merged.agents[seen_agents[agent.name]] = agent
      else:
        seen_agents[agent.name] = len(merged.agents)
        merged.agents.append(agent)

    merged.mcp_servers.update(manifest.mcp_servers)

  return merged


def format_manifest_summary(manifest: PluginManifest) -> str:
  """Return a system-prompt friendly summary of a merged plugin manifest."""
  lines: list[str] = ["## Project Plugins"]

  if manifest.name:
    lines.append(f"Plugin bundle: {manifest.name}")
  if manifest.description:
    lines.append(manifest.description)

  if manifest.skills:
    lines.append("\nSkills:")
    for skill in manifest.skills:
      lines.append(f"  - {skill.name}: {skill.description or 'no description'}")

  if manifest.agents:
    lines.append("\nAgents:")
    for agent in manifest.agents:
      lines.append(f"  - {agent.name}: {agent.description or 'no description'}")

  if manifest.mcp_servers:
    lines.append("\nMCP servers:")
    for name in manifest.mcp_servers:
      lines.append(f"  - {name}")

  if manifest.commands:
    lines.append("\nCustom commands:")
    for cmd in manifest.commands:
      lines.append(f"  - {cmd.name}: {cmd.description or cmd.command}")

  if manifest.allowed_tools:
    lines.append(f"\nAllowed tools: {', '.join(manifest.allowed_tools)}")

  return "\n".join(lines)
