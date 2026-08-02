from pathlib import Path

from nonoka import SkillRegistry

from nonoka_cli.core.skill_routing import required_skill_names


def test_required_skill_names_uses_skill_declared_metadata(tmp_path: Path):
  skill_dir = tmp_path / "context-helper"
  skill_dir.mkdir()
  (skill_dir / "SKILL.md").write_text(
    """---
name: context-helper
description: Context helper
metadata:
  activation:
    mode: required
    triggers: [context7, docs]
---
# Context Helper
""",
    encoding="utf-8",
  )
  registry = SkillRegistry(enabled=["context-helper"], search_paths=[tmp_path])

  assert required_skill_names("Please connect Context7 MCP", registry) == ["context-helper"]
  assert required_skill_names("Please review this implementation", registry) == []
