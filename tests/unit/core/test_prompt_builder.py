from nonoka_cli.core.prompt_builder import SystemPromptBuilder


def test_prompt_builder_injects_verification_discipline_once():
  prompt = SystemPromptBuilder(base="Build the requested change.", model="test-model").build()

  assert "## Verification Discipline" in prompt
  assert "Command exit status alone is not proof" in prompt
  assert "invoke it through the appropriate test runner" in prompt

  existing = SystemPromptBuilder(
    base="Build the requested change.\n\n## Verification Discipline\nUse pytest.",
    model="test-model",
  ).build()
  assert existing.count("## Verification Discipline") == 1


def test_prompt_builder_identifies_the_authoritative_config_file():
  prompt = SystemPromptBuilder(
    base="Build the requested change.",
    model="test-model",
    cwd="/tmp/workspace",
    config_path="/tmp/workspace/nonoka/config/config.yaml",
  ).build()

  assert "## Active Nonoka Configuration" in prompt
  assert "`/tmp/workspace/nonoka/config/config.yaml`" in prompt
  assert "instead of guessing `~/.config/nonoka/config.yaml`" in prompt
  assert "`mcp_servers`" in prompt


def test_prompt_builder_requires_metadata_matched_skills_before_work_tools():
  prompt = SystemPromptBuilder(
    base="Build the requested change.",
    model="test-model",
    required_skills=["example-skill"],
  ).build()

  assert "## Required Skill Activation" in prompt
  assert "`example-skill`" in prompt
  assert "Before any non-todowrite tool call" in prompt
