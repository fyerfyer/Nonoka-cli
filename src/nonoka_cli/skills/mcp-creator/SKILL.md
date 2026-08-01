---
name: mcp-creator
description: Configure a project MCP server safely in Nonoka and validate it after reload.
---

# MCP Creator

Use this skill when the user asks to add, remove, or change an MCP server.

1. Inspect the active Nonoka config and existing `mcp_servers` entries.
2. Collect the server name, transport, command, arguments, and required
   environment variables. Never print secret values.
3. Make the smallest valid YAML edit under `mcp_servers`; preserve unrelated
   servers and permission settings.
4. Explain any package installation or network access that would be required
   and obtain explicit approval before performing it.
5. Ask the user to run `/reload`, then inspect the MCP status or make a small
   read-only tool call to verify the server is available.

Do not silently enable a server-wide auto-approval policy just because a new
MCP server is being configured.
