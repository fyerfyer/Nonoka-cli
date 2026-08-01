---
name: subagent-creator
description: Add a bounded project subagent through .nonoka/plugin.json.
---

# Subagent Creator

Use this skill when the user asks for a specialist, reviewer, planner, or
other project subagent.

1. Inspect `.nonoka/plugin.json` if it exists; preserve all unrelated plugin
   entries.
2. Add or update an `agents` entry with a clear name, description, model,
   system prompt, bounded `max_turns` and `max_invocations`, and the minimum
   `allowed_tools` necessary.
3. Prefer advisory/read-only agents by default. Explain any file-write or shell
   authority before granting it.
4. Validate the JSON and request `/reload` so the bridge discovers the new
   manifest and rebuilds the agent.

Never create an unbounded recursive agent or silently copy the primary
agent's full permissions into a subagent.
