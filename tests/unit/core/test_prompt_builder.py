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
