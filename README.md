# nonoka-cli

OpenCode backend for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

`nonoka-cli` runs as a stdio NDJSON bridge server (`nonoka-cli --server`) that
talks to the `nonoka-opencode-provider` TypeScript package. When used inside
OpenCode, Nonoka acts as the conversation/decision backend while OpenCode owns
tool execution and human-in-the-loop (HITL) approval using its native tools.

## Quick install

The easiest way to get nonoka + OpenCode is the one-line installer:

```bash
curl -fsSL https://nonoka.dev/install.sh | bash
```

The installer will:

1. Check Python 3.10+ and Node/npm.
2. Install or update OpenCode.
3. Install `nonoka-cli` and the OpenCode provider.
4. Generate `~/.config/nonoka/config.yaml` and `~/.config/opencode/opencode.json`.

After installing, configure your API key and run `opencode`:

```bash
# Interactive: it will ask for your key and save it to ~/.config/nonoka/.env
nonoka-cli config init

# Or set it manually
export DEEPSEEK_API_KEY=<your-key>

nonoka-cli doctor
opencode
```

`nonoka-cli` automatically loads `~/.config/nonoka/.env` and `./.env` on startup,
so you don't need to `export` every time if you save the key in `.env`.

> To use `uv` instead of `pip`, or to run non-interactively, pass flags:
> `curl -fsSL https://nonoka.dev/install.sh | bash -s -- --uv --yes`.

## Manual installation

```bash
# Install nonoka-cli
pip install nonoka-cli
# or with uv
uv pip install nonoka-cli

# Install the OpenCode provider globally so OpenCode can load it
npm install -g nonoka-opencode-provider
```

## Quick start

1. Create your nonoka config (it will ask for your API key and save it to
   `~/.config/nonoka/.env`):

```bash
nonoka-cli config init
```

For scripted setups, use the non-interactive mode (you'll still need to set the
API key via `.env` or `export`):

```bash
nonoka-cli config init --yes --model deepseek-chat
```

2. Generate an OpenCode config in the current project or globally:

```bash
# Project-level
nonoka-cli opencode init

# User-level
nonoka-cli opencode init --global
```

3. Make sure your model API key is exported, then run:

```bash
opencode
```

## `nonoka-cli doctor`

Diagnose your installation and configuration:

```bash
nonoka-cli doctor
```

Example output:

```
nonoka-cli doctor
✓ nonoka-cli 0.2.4
✓ Python 3.11
✓ opencode 1.17.13
✓ provider nonoka-opencode-provider@0.2.4
✓ config ~/.config/nonoka/config.yaml
✓ API key DEEPSEEK_API_KEY set
✓ OpenCode provider config in /home/user/.config/opencode/opencode.json
```

If anything is wrong, `doctor` prints a remedy line. To also verify the LLM API
key with a real (small) call, use:

```bash
nonoka-cli doctor --check-llm
```

## Configuration

### `nonoka-cli config init`

Interactive wizard that writes `~/.config/nonoka/config.yaml`. It asks for a
model identifier (e.g. `deepseek-chat`, `openai/gpt-4o`, `ollama/llama3.3`), a
masked API key, and whether to save it to `~/.config/nonoka/.env` (recommended),
directly in `config.yaml`, or skip saving. It also asks for a system prompt and
whether to auto-approve all tool calls.

Non-interactive example:

```bash
nonoka-cli config init --yes --model openai/gpt-4o
```

### `nonoka-cli config set <key> <value>`

Update a single config value. Dotted keys are supported:

```bash
nonoka-cli config set model openai/gpt-4o
nonoka-cli config set cli.theme light
nonoka-cli config set hitl.dangerous_tools '["write_file", "execute_command"]'
```

### `nonoka-cli config show`

Print the resolved configuration and its file path.

### `nonoka-cli opencode init`

Generate or merge an `opencode.json` in the current directory and create
`.opencode/agents/build.md` from your nonoka `system_prompt`. The generated
config points OpenCode at the `nonoka-opencode-provider` package and passes the
nonoka config path to the backend.

## OpenCode configuration

`nonoka-cli opencode init` generates two things:

1. `opencode.json` in the current directory, which wires OpenCode to the
   `nonoka-opencode-provider` package and sets HITL permissions.
2. `.opencode/agents/build.md`, which contains the agent prompt.

A typical generated `opencode.json` looks like:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "nonoka/default",
  "provider": {
    "nonoka": {
      "npm": "nonoka-opencode-provider",
      "name": "Nonoka",
      "options": {
        "serverCommand": ["bash", "-c", "nonoka-cli --server 2>/tmp/nonoka-server.log"],
        "cwd": ".",
        "configPath": "~/.config/nonoka/config.yaml"
      },
      "models": {
        "default": { "name": "Nonoka deepseek-chat" }
      }
    }
  },
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask"
  },
  "agent": {
    "build": {
      "mode": "primary",
      "permission": {
        "*": "ask",
        "bash": "ask",
        "edit": "ask",
        "write": "ask"
      }
    }
  }
}
```

## Prompt ownership

Nonoka owns the canonical system prompt via `system_prompt` in
`~/.config/nonoka/config.yaml`. When you run `nonoka-cli opencode init`, the
command adapts that prompt and writes it to `.opencode/agents/build.md` so
OpenCode uses it for its primary agent. OpenCode-specific guidelines (tool
names, approval behavior, path conventions) are appended automatically; they are
not mixed into Nonoka's core prompt, so the same config works for other
frontends in the future.

## Human-in-the-loop

When running inside OpenCode, HITL is handled by OpenCode itself. The generated
`opencode.json` sets `"*": "ask"` so every tool requires approval. Because
Nonoka forwards OpenCode's native tool definitions to the model, approval
dialogs render natively for `bash`, `read`, `write`, and `edit` operations.

If you prefer auto-approval, change the permissions in `opencode.json` or set
`cli.auto_approve: true` / `hitl.policy: auto` in `nonoka.yaml` for Nonoka's
standalone mode.

## External-tools mode

When nonoka-cli runs inside OpenCode, it operates in **external-tools mode** by
default. OpenCode sends its native tool list (e.g. `bash`, `read`, `write`,
`edit`, `todowrite`) to the provider; nonoka-cli registers them as
`ExternalCapability` objects and lets OpenCode execute them. This means:

- OpenCode owns tool execution, HITL approval, and TUI rendering.
- nonoka owns decision-making: which tool to call, when, and with what arguments.
- Tool results are returned by OpenCode and resumed via
  `Runner.resume_external_tools()`.

To start external-tools mode, run OpenCode with the generated `opencode.json`;
the provider spawns `nonoka-cli --server` automatically.

## MCP and Skill support

nonoka-cli can merge MCP tools and lazy-loaded skills alongside OpenCode's
native tools. Configure them in `~/.config/nonoka/config.yaml`:

```yaml
model: deepseek-chat

mcp_servers:
  filesystem:
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]

skills:
  - code-review
  - nextjs-best-practices
```

- **MCP tools** are executed locally by nonoka-cli and are exposed with a
  `mcp__<server>__<tool>` namespace prefix so they do not collide with OpenCode
  native tools.
- **Skills** use nonoka-agent's lazy `SkillRegistry`. Only names and
  descriptions are injected into the system prompt; full guidance is loaded
  on-demand via the `load_skill` tool. Skill tools are prefixed with
  `skill__<skill>__<tool>` in external-tools mode.

Both MCP tools and skill tools remain available in standalone mode without any
prefixing.

## Known limitations

These are current behaviors observed with OpenCode CLI 1.17.13. They are tracked
here because they affect the TUI/HITL experience but cannot be fixed inside
`nonoka-cli` or `nonoka-opencode-provider`.

- [x] **External directory rejection crashes OpenCode**: the adapter now injects
  the current working directory into the system prompt and instructs the model
  to use paths relative to it, so requests outside the workspace are rare. If
  one still occurs and you select **Reject**, OpenCode may still exit. Keep
  requests scoped to the current working directory, or approve if the path is
  safe.
- [ ] **`write` is auto-approved inside the workspace**: even with `"*": "ask"`
  in `opencode.json`, OpenCode does not show an approval dialog for `write`
  operations within the workspace root. `bash`, `read`, and `edit` do ask.
- [ ] **Code blocks render as plain indented text**: OpenCode renders Python and
  other code as plain indented output rather than fenced code blocks with syntax
  highlighting. This is an OpenCode TUI rendering choice.
- [ ] **Short replies leave empty vertical space**: the OpenCode TUI uses a flex
  layout, so short assistant replies appear at the top with visible empty space
  above the status bar. This is normal OpenCode layout behavior.
- [ ] **Model may skip tools for ambiguous requests**: the adapter prompt
  mitigates this, but a vague request can still cause the model to answer
  directly instead of calling `read`/`edit`. Make file/tool requests explicit.

## Server logs and request traces

When running inside OpenCode, the provider spawns `nonoka-cli --server` as a
long-lived NDJSON bridge. Server stderr is redirected by the provider to a
per-working-directory log file so it does not pollute OpenCode's TUI:

```text
/tmp/nonoka-server-<cwd-hash>.log
```

In addition, `nonoka-cli --server` writes a structured NDJSON trace of every
request and stream event for debugging:

```text
/tmp/nonoka-trace/trace-YYYYMMDD.jsonl
```

You can override the trace directory with the `NONOKA_TRACE_DIR` environment
variable.

### Debug environment variables

| Variable | Effect |
| --- | --- |
| `NONOKA_DEBUG=1` | Emit `debug` NDJSON events from the bridge for every request and stream transition. |
| `NONOKA_TRACE_DIR=/path` | Directory for NDJSON request/event traces (default: `/tmp/nonoka-trace`). |
| `NONOKA_SERVER_LOG=/path` | Override the server stderr log path when running the bridge manually. |

## Development

```bash
# Install in editable mode
uv pip install -e .

# Run the bridge server
nonoka-cli --server --config ./nonoka.yaml

# Lint and test
uv run --no-sync ruff check .
uv run --no-sync pytest tests/unit
```

## Project layout

```text
src/nonoka_cli/
├── bridge/          # NDJSON protocol, request handler, server
├── commands/        # CLI subcommands (config, doctor, opencode)
├── config/          # YAML config loading and Pydantic models
├── core/            # Orchestrator, RunnerService, SessionService, ToolService,
│                    # MCPService, AgentFactory, prompt/context/task-state/output pruning
│                    #   agent_factory.py      # Build nonoka Agent from CLI config
│                    #   prompt_builder.py     # System prompt assembly for OpenCode mode
│                    #   context_trimmer.py    # Turn-based context window trimming
│                    #   task_state.py         # Local TODO state mirror
│                    #   tool_output_policy.py # Tool output pruning / spill policy
├── mcp/             # MCP server lifecycle manager (thin wrapper around nonoka-agent)
├── sessions/        # Session metadata persistence
├── skills/          # Skill loading shim (delegates to nonoka-agent SkillRegistry)
├── tools/           # Built-in and local tool loader
└── utils/           # Errors, logging, trace logger

packages/nonoka-opencode-provider/  # TypeScript provider for OpenCode
install.sh                          # One-line installer
```

## License and attribution

`nonoka-cli` and `nonoka-opencode-provider` are released under the MIT License.

The terminal TUI and OpenCode client/server architecture are provided by
[OpenCode](https://github.com/anomalyco/opencode) (MIT License). The agent
core is provided by the [Nonoka](https://pypi.org/project/nonoka/) framework.

See `LICENSE` and `NOTICE` for full details.
