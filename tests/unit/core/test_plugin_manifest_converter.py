"""Tests for plugin manifest conversion to OpenCode."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.core.plugin_manifest import (
  PluginManifest,
  SkillEntry,
)
from nonoka_cli.core.plugin_manifest_converter import (
  convert_to_opencode,
  write_opencode_files,
)


def test_convert_to_opencode_adds_skill_permission() -> None:
  manifest = PluginManifest(
    name="test-plugin",
    skills=[
      SkillEntry(name="review", description="Review code"),
    ],
  )
  snippet = convert_to_opencode(manifest)
  assert snippet == {"permission": {"skill": {"*": "allow"}}}


def test_convert_to_opencode_empty_when_no_skills() -> None:
  manifest = PluginManifest(name="empty-plugin")
  assert convert_to_opencode(manifest) == {}


def test_convert_to_opencode_maps_allowed_tools() -> None:
  manifest = PluginManifest(
    name="test-plugin",
    allowed_tools=["read_file", "execute_command"],
  )
  snippet = convert_to_opencode(manifest)
  assert snippet["permission"]["read_file"] == "allow"
  assert snippet["permission"]["execute_command"] == "allow"


def test_write_opencode_files_creates_skill_markdown(tmp_path: Path) -> None:
  manifest = PluginManifest(
    name="test-plugin",
    skills=[
      SkillEntry(
        name="review",
        description="Review code",
        activation_prompt="When reviewing, focus on bugs.",
      ),
    ],
  )
  written = write_opencode_files(manifest, tmp_path)
  assert len(written) == 1
  skill_file = tmp_path / ".opencode" / "skills" / "review" / "SKILL.md"
  assert skill_file.exists()
  text = skill_file.read_text(encoding="utf-8")
  assert "name: review" in text
  assert "description: Review code" in text
  assert "When reviewing, focus on bugs." in text
