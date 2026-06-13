"""Tests for skill loading and application."""

from __future__ import annotations

from nonoka import Agent

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.skills.manager import SkillInfo, SkillManager


class TestSkillManager:
  """Tests for SkillManager."""

  def test_load_all_returns_empty_when_no_skills(self):
    manager = SkillManager(search_paths=[])
    skills = manager.load_all()
    assert skills == []

  def test_discover_skills_in_directory(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "code-review.md").write_text(
      """---
name: code-review
description: Reviews code changes.
---
When reviewing code, focus on correctness, readability, and tests.
"""
    )

    manager = SkillManager(search_paths=[skills_dir])
    skills = manager.load_all()

    assert len(skills) == 1
    assert skills[0].name == "code-review"

  def test_load_all_filters_by_enabled_names(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a.md").write_text("---\nname: a\n---\n")
    (skills_dir / "b.md").write_text("---\nname: b\n---\n")

    manager = SkillManager(search_paths=[skills_dir])
    skills = manager.load_all(enabled=["a"])

    assert len(skills) == 1
    assert skills[0].name == "a"

  def test_load_all_by_explicit_file_path(self, tmp_path):
    skill_file = tmp_path / "my-skill.md"
    skill_file.write_text("---\nname: my-skill\n---\n")

    manager = SkillManager(search_paths=[])
    skills = manager.load_all(enabled=[str(skill_file)])

    assert len(skills) == 1
    assert skills[0].name == "my-skill"

  def test_apply_to_merges_system_prompt(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "reviewer.md").write_text(
      "---\nname: reviewer\nsystem_prompt: Focus on security.\n---\nCheck for SQL injection."
    )

    manager = SkillManager(search_paths=[skills_dir])
    agent = Agent(model="gpt-4o", system_prompt="You are helpful.")
    merged = manager.apply_to(agent, ["reviewer"])

    assert "You are helpful." in merged.system_prompt
    assert "Focus on security." in merged.system_prompt
    assert "Check for SQL injection" in merged.system_prompt

  def test_list_available(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "x.md").write_text("---\nname: x\ndescription: desc x\n---\n")

    manager = SkillManager(search_paths=[skills_dir])
    available = manager.list_available()

    assert len(available) == 1
    assert isinstance(available[0], SkillInfo)
    assert available[0].name == "x"
    assert available[0].description == "desc x"

  def test_list_loaded_after_load_all(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "y.md").write_text("---\nname: y\n---\n")

    manager = SkillManager(search_paths=[skills_dir])
    manager.load_all(enabled=["y"])
    loaded = manager.list_loaded()

    assert [info.name for info in loaded] == ["y"]


class TestAgentFactoryWithSkills:
  """Tests for AgentFactory skill integration."""

  def test_build_applies_configured_skills(self, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "tester.md").write_text(
      "---\nname: tester\nsystem_prompt: Always write tests.\n---\n"
    )

    config = CLIConfig(
      model="gpt-4o",
      system_prompt="You are helpful.",
      skills=["tester"],
    )
    skill_manager = SkillManager(search_paths=[skills_dir])
    factory = AgentFactory(config, skill_manager=skill_manager)
    agent = factory.build()

    assert "You are helpful." in agent.system_prompt
    assert "Always write tests." in agent.system_prompt
