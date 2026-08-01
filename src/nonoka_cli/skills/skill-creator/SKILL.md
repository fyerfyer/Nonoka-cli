---
name: skill-creator
description: Create or update a project-local Nonoka skill that can be activated in OpenCode.
---

# Skill Creator

Use this skill when the user asks to add, revise, or package a reusable Nonoka
skill. Work only in the current project unless the user explicitly requests a
user-level skill.

1. Clarify the skill name, trigger, expected workflow, and any tools it needs.
2. Create `.nonoka/skills/<name>/SKILL.md` with YAML frontmatter (`name` and
   `description`) followed by focused operating instructions.
3. Preserve existing project skills and do not overwrite one without showing
   the intended change.
4. Ensure the name is present in `skills` in the active Nonoka config.
5. Tell the user to run `/reload` after the file/config change, then confirm
   the skill is discoverable before claiming success.

Do not place credentials in a skill file. Do not install dependencies or grant
new permissions without the user's explicit approval.
