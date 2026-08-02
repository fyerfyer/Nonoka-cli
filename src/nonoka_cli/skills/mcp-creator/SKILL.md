---
name: mcp-creator
description: Configure a project MCP server safely in Nonoka and validate its lifecycle.
---

# MCP Creator

Use this skill when the user asks to add, remove, or change an MCP server.

1. Inspect the active Nonoka config and existing `mcp_servers` entries.
2. Collect the server name, transport, command, arguments, and required
   environment variables. Never print secret values.
   Confirm its actual network hosts from the upstream documentation or package
   metadata; a user-provided hostname is a request to permit that host, not
   proof that it is the one the MCP runtime contacts. For example, the current
   `@upstash/context7-mcp` local server downloads from `registry.npmjs.org`
   and calls `context7.com` (not `api.context7.com`).
3. Inspect `safety.network_profile` before proposing network changes:
   - With `strict`, list every required download and runtime host in
     `safety.allowed_domains`, or ask for approval to switch to
     `package-registries` when official npm/PyPI package downloads are the
     only extra hosts.
   - With `package-registries`, do not redundantly add npm/PyPI distribution
     hosts. Add each MCP runtime API host explicitly to
     `safety.allowed_domains`.
   - Never introduce a wildcard, open-network profile, or change the profile
     without the user's approval.
4. Make the smallest valid YAML edit under `mcp_servers`; preserve unrelated
   servers and permission settings. If the MCP needs new SRT domains, add its
   `mcp_servers` entry and all required `safety.allowed_domains` in the same
   edit. Do not leave a partially configured networked MCP for the next turn.
   A stdio server entry must include every required field below. In particular,
   `transport: stdio` is required; never infer or omit it. Use the exact
   timeout field name `startup_timeout_seconds` (not `start_timeout_seconds`
   or another alias):

   ```yaml
   mcp_servers:
     example:
       transport: stdio
       command: npx
       args:
         - -y
         - "@scope/server-package"
       startup_timeout_seconds: 30
   ```

   Before saving, check every new stdio server entry against this structure so
   the config remains loadable after the required restart.
5. Explain any package installation or network access that would be required
   and obtain explicit approval before performing it.
6. If only the MCP definition changed, ask the user to run `/reload`, then
   inspect MCP status or make one small read-only tool call to verify it.
7. If the change also updates `safety.allowed_domains` or other SRT sandbox
   settings, explain that `/reload` cannot replace the outer SRT process-tree
   policy. Ask the user to exit the TUI and launch `nonoka` again, then verify
   the MCP. Do not investigate unrelated source code after reporting this
   required restart.

Do not silently enable a server-wide auto-approval policy just because a new
MCP server is being configured.
