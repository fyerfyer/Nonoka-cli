"""Tests for plugin manifest loading and formatting."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.core.plugin_manifest import (
  AgentEntry,
  DynamicAgentEntry,
  LoadedPluginManifest,
  PluginManifest,
  PluginManifestLoader,
  format_manifest_summary,
  merge_manifests,
)
from nonoka_cli.core.project_agents import (
  effective_agent_definitions,
  effective_dynamic_agent_definition,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
  return tmp_path / "project"


def test_load_default_manifest(repo: Path) -> None:
  manifest_dir = repo / ".nonoka"
  manifest_dir.mkdir(parents=True)
  (manifest_dir / "plugin.json").write_text(
    '{"name": "test-plugin", "skills": [{"name": "python"}]}'
  )

  loader = PluginManifestLoader()
  manifests = loader.load(repo)
  assert len(manifests) == 1
  assert manifests[0].name == "test-plugin"
  assert manifests[0].skills[0].name == "python"

  loaded = loader.load_with_sources(repo)
  assert loaded[0].path == (manifest_dir / "plugin.json").resolve()
  assert loaded[0].manifest.name == "test-plugin"


def test_effective_agent_definition_keeps_winning_source(tmp_path: Path) -> None:
  first = tmp_path / "first.json"
  second = tmp_path / "second.json"
  loaded = [
    LoadedPluginManifest(
      path=first,
      manifest=PluginManifest(
        agents=[AgentEntry(name="reviewer", model="old", system_prompt="old")]
      ),
    ),
    LoadedPluginManifest(
      path=second,
      manifest=PluginManifest(
        agents=[AgentEntry(name="reviewer", model="new", system_prompt="new")]
      ),
    ),
  ]

  definition = effective_agent_definitions(loaded)[0]

  assert definition.entry.model == "new"
  assert definition.source == second


def test_effective_dynamic_agent_policy_keeps_winning_source(tmp_path: Path) -> None:
  first = tmp_path / "first.json"
  second = tmp_path / "second.json"
  loaded = [
    LoadedPluginManifest(
      path=first,
      manifest=PluginManifest(dynamic_agent=DynamicAgentEntry(enabled=True, model="old")),
    ),
    LoadedPluginManifest(
      path=second,
      manifest=PluginManifest(dynamic_agent=DynamicAgentEntry(enabled=True, model="new")),
    ),
  ]

  definition = effective_dynamic_agent_definition(loaded)

  assert definition is not None
  assert definition.entry.model == "new"
  assert definition.source == second


def test_merge_manifests_combines_lists_and_dicts() -> None:
  m1 = PluginManifest(name="a", skills=[{"name": "s1"}], agents=[{"name": "planner"}])
  m2 = PluginManifest(
    name="b",
    skills=[{"name": "s2"}],
    agents=[{"name": "planner", "description": "updated"}],
    mcp_servers={"test": MCPServerConfigModel(transport="stdio", command="echo")},
    dynamic_agent={"enabled": True, "model": "child"},
  )
  merged = merge_manifests([m1, m2])
  assert [s.name for s in merged.skills] == ["s1", "s2"]
  assert len(merged.agents) == 1
  assert merged.agents[0].description == "updated"
  assert "test" in merged.mcp_servers
  assert merged.dynamic_agent is not None
  assert merged.dynamic_agent.model == "child"


def test_format_manifest_summary() -> None:
  manifest = PluginManifest(
    name="bundle",
    description="A test bundle",
    skills=[{"name": "python", "description": "Python skill"}],
    agents=[{"name": "planner"}],
    mcp_servers={"test": MCPServerConfigModel(transport="stdio", command="echo")},
    allowed_tools=["read_file"],
    dynamic_agent={"enabled": True, "model": "child"},
  )
  summary = format_manifest_summary(manifest)
  assert "bundle" in summary
  assert "Python skill" in summary
  assert "planner" in summary
  assert "test" in summary
  assert "read_file" in summary
  assert "agent__spawn" in summary
