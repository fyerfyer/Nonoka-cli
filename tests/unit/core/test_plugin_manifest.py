"""Tests for plugin manifest loading and formatting."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.config.models import MCPServerConfigModel
from nonoka_cli.core.plugin_manifest import (
  PluginManifest,
  PluginManifestLoader,
  format_manifest_summary,
  merge_manifests,
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


def test_merge_manifests_combines_lists_and_dicts() -> None:
  m1 = PluginManifest(name="a", skills=[{"name": "s1"}], agents=[{"name": "planner"}])
  m2 = PluginManifest(
    name="b",
    skills=[{"name": "s2"}],
    agents=[{"name": "planner", "description": "updated"}],
    mcp_servers={"test": MCPServerConfigModel(transport="stdio", command="echo")},
  )
  merged = merge_manifests([m1, m2])
  assert [s.name for s in merged.skills] == ["s1", "s2"]
  assert len(merged.agents) == 1
  assert merged.agents[0].description == "updated"
  assert "test" in merged.mcp_servers


def test_format_manifest_summary() -> None:
  manifest = PluginManifest(
    name="bundle",
    description="A test bundle",
    skills=[{"name": "python", "description": "Python skill"}],
    agents=[{"name": "planner"}],
    mcp_servers={"test": MCPServerConfigModel(transport="stdio", command="echo")},
    allowed_tools=["read_file"],
  )
  summary = format_manifest_summary(manifest)
  assert "bundle" in summary
  assert "Python skill" in summary
  assert "planner" in summary
  assert "test" in summary
  assert "read_file" in summary
