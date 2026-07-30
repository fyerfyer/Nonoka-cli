"""Plugin manifest loader for nonoka-cli.

Supports ``.nonoka/plugin.json`` files that declare skills, agents, MCP servers,
commands, hooks, and allowed tools. The schema is intentionally close to formats
used by Claude Code, Continue, and OpenCode so manifests can be converted
between ecosystems.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator

from nonoka_cli.config.models import MCPServerConfigModel

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
  max_turns: int = 3
  max_invocations: int = 2
  allowed_tools: list[str] = Field(default_factory=list)
  output_contract: Literal["text", "review"] = "text"


class DynamicAgentEntry(BaseModel):
  """Policy for the bounded dynamic advisory-agent tool.

  The caller may describe a role and task, but cannot select a model, grant
  tools, or change execution budgets.  Those authority-bearing choices remain
  project configuration.
  """

  enabled: bool = False
  model: str = ""
  base_system_prompt: str = (
    "You are a temporary advisory sub-agent. Work only from the supplied task "
    "and context. Return a concise, actionable answer to the parent agent."
  )
  description: str = "Create a temporary, tool-free advisory sub-agent."
  max_turns: int = 2
  max_invocations: int = 2
  max_role_chars: int = 80
  max_instruction_chars: int = 2000
  max_task_chars: int = 8000
  max_context_chars: int = 16000


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
  dynamic_agent: DynamicAgentEntry | None = None
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
    candidates = [working_dir / p for p in DEFAULT_MANIFEST_PATHS] + [
      working_dir / p for p in self._extra_paths
    ]
    discovered: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
      resolved = path.resolve()
      if resolved not in seen and resolved.exists() and resolved.is_file():
        seen.add(resolved)
        discovered.append(resolved)
    return discovered

  def load(self, working_dir: Path) -> list[PluginManifest]:
    """Load all manifests discovered in *working_dir*."""
    return [loaded.manifest for loaded in self.load_with_sources(working_dir)]

  def load_path(self, path: Path) -> LoadedPluginManifest | None:
    """Load one exact manifest path, retaining its resolved source."""
    resolved = path.expanduser().resolve()
    try:
      data = json.loads(resolved.read_text(encoding="utf-8"))
      manifest = PluginManifest.model_validate(data)
      logger.info("plugin_manifest_loaded", path=str(resolved), name=manifest.name)
      return LoadedPluginManifest(path=resolved, manifest=manifest)
    except json.JSONDecodeError as exc:
      logger.warning("plugin_manifest_invalid_json", path=str(resolved), error=str(exc))
    except Exception as exc:
      logger.warning("plugin_manifest_load_failed", path=str(resolved), error=str(exc))
    return None

  def load_with_sources(self, working_dir: Path) -> list[LoadedPluginManifest]:
    """Load manifests together with their resolved source paths."""
    manifests: list[LoadedPluginManifest] = []
    for path in self.discover(working_dir):
      loaded = self.load_path(path)
      if loaded is not None:
        manifests.append(loaded)
    return manifests


@dataclass(frozen=True)
class LoadedPluginManifest:
  """A validated manifest paired with the file that declared it."""

  path: Path
  manifest: PluginManifest


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
    if manifest.dynamic_agent is not None:
      merged.dynamic_agent = manifest.dynamic_agent

  return merged


def format_manifest_summary(
  manifest: PluginManifest,
  agent_tool_names: list[str] | None = None,
) -> str:
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

  if agent_tool_names is None and manifest.agents:
    lines.append("\nAgents:")
    for agent in manifest.agents:
      lines.append(f"  - {agent.name}: {agent.description or 'no description'}")
  elif agent_tool_names:
    lines.append("\nProject advisory agent tools:")
    for name in agent_tool_names:
      lines.append(f"  - {name}")

  if manifest.dynamic_agent and manifest.dynamic_agent.enabled:
    lines.append("\nDynamic advisory agent: enabled as agent__spawn")

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
