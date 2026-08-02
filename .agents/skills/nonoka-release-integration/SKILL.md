---
name: nonoka-release-integration
description: Validate the latest published nonoka-cli release through the GitHub installer and a real OpenCode TUI task. Use when testing release installation, SRT network policy, MCP or plugin setup, native tool rendering, streaming, or end-to-end agent behavior that unit tests and local editable installs cannot cover.
---

# Nonoka Release Integration

Exercise a released artifact, not a development checkout. This workflow exists
because editable installs, source-level tests, and synthetic tool calls cannot
prove the GitHub installer, generated OpenCode provider, outer SRT sandbox,
and interactive TUI work together.

## Inputs And Guardrails

- Obtain the concrete task to give Nonoka and confirmation that a live model
  call and any associated cost are in scope.
- Default the target to `/home/fyerfyer/fyerfyer/scripts/test`. Only remove
  that directory after the user explicitly authorizes the deletion; otherwise
  create a unique directory under `/tmp`.
- Never substitute `--dev`, a local wheel, an editable package, or a later
  `uv pip install` for the released installer flow.
- Do not print API keys, proxy credentials, or broad process listings. Load
  model credentials only inside the tmux launch shell.
- Do not manually implement the feature being tested. Preconfigure only the
  test policy the user selected; Nonoka must perform the requested MCP,
  plugin, or task configuration in the TUI.

## Installation

1. Download the current installer from GitHub to a unique `/tmp` path with
   `curl -fsSL https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh`.
2. Make the target directory, `cd` into it, and run that downloaded script
   with isolated locations:

   ```bash
   env NONOKA_INSTALL_DIR="$TEST_ROOT/nonoka" \
     NONOKA_CONFIG_DIR="$TEST_ROOT/nonoka/config" \
     NONOKA_NPM_PREFIX="$TEST_ROOT/nonoka/npm" \
     bash "$INSTALLER" --yes --npm-opencode
   ```

3. Run `"$TEST_ROOT/nonoka/bin/nonoka" --version` and `doctor` from
   `$TEST_ROOT`. Treat a missing API key in the ordinary shell as expected if
   credentials are intentionally loaded only for the TUI. The installer,
   generated project `opencode.json`, provider, and SRT smoke test must pass
   before starting a live task.

## SRT Policy

Keep `safety.network_profile: strict` unless the test needs official npm or
PyPI package downloads. With explicit user approval, set
`network_profile: package-registries`; it permits only the official
distribution hosts. It does not permit an MCP's business API.

For an MCP setup task, instruct Nonoka to load `mcp-creator`, discover the
runtime host, and add that host to `safety.allowed_domains` itself. Do not
hard-code one MCP or one hostname into this integration skill. A change to
either `allowed_domains` or `network_profile` requires a full TUI restart;
`/reload` cannot replace the outer SRT policy.

## Live TUI Run

1. Start the installed launcher in a named tmux session from `$TEST_ROOT`.
   Source the local credential file only inside that session, then capture the
   pane until the OpenCode welcome screen appears.
2. Send the user's task verbatim. Inspect the pane during execution using
   `tmux capture-pane`; this is required evidence, not an optional log.
3. If OpenCode displays its own host-tool permission UI, identify it as an
   OpenCode permission rather than Nonoka HITL. Grant only the needed,
   isolated test-directory permission after reviewing the displayed action.
4. For an MCP task, confirm a native `load_skill` card, the generated valid
   `mcp_servers` entry, and the required SRT domain. Restart the TUI when
   Nonoka changes SRT policy, then give it a small read-only MCP task.
5. Keep the tmux session alive until the user has had a chance to inspect it,
   unless they ask for cleanup.

## Completion Evidence

Report the installed version, installer and doctor result, model/task, TUI
duration, observable native tool calls, final task result, and the exact
configuration fields changed by Nonoka. Separate failures into installer,
provider/TUI, OpenCode permission, SRT policy, MCP startup, model, or task
execution; do not label an unobserved timeout as a model failure.
