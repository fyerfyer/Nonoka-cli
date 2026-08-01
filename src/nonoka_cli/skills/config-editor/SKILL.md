---
name: config-editor
description: Safely edit Nonoka configuration and apply it with the OpenCode /reload command.
---

# Config Editor

Use this skill for requests to change model, permissions, safety, tools,
skills, budgets, cache, or other Nonoka settings.

1. Read the active config file before editing it and state which setting will
   change.
2. Preserve unrelated YAML keys, comments where practical, and `.env` secrets.
3. Validate types and keep destructive permissions as `ask` unless the user
   explicitly asks for auto approval.
4. Never write API keys into config.yaml when the adjacent `.env` file is the
   appropriate location.
5. After a successful edit, tell the user to use `/reload`; that reloads the
   running bridge and rebuilds the agent without restarting OpenCode.

For a change that modifies the generated `opencode.json` or agent prompt, run
`nonoka init` after confirmation, then use `/reload`.
