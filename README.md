# nonoka-cli

OpenCode backend for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

`nonoka-cli` runs as a stdio NDJSON bridge server (`nonoka-cli --server`) that
talks to the `nonoka-opencode-provider` TypeScript package. OpenCode uses the
provider to drive Nonoka agents, with full support for tool cards and
human-in-the-loop (HITL) approval.

## Installation

```bash
pip install nonoka-cli
# or with uv
uv pip install nonoka-cli
```

Install the OpenCode provider (globally so OpenCode can load it):

```bash
npm install -g nonoka-opencode-provider
```

## Quick start

1. Create your nonoka config:

```bash
nonoka-cli config init
```

2. Generate an OpenCode config in the current project:

```bash
nonoka-cli opencode init
```

3. Make sure your model API key is exported, then run:

```bash
opencode
```

## Configuration

### `nonoka-cli config init`

Interactive wizard that writes `~/.config/nonoka/config.yaml`. It asks for a
model identifier (e.g. `deepseek-chat`, `openai/gpt-4o`, `ollama/llama3.3`), an
optional API-key environment variable, a system prompt, and whether to
auto-approve all tool calls.

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

Generate or merge an `opencode.json` in the current directory. The generated
config points OpenCode at the `nonoka-opencode-provider` package and passes the
nonoka config path to the backend.

## OpenCode configuration

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
        "serverCommand": ["nonoka-cli", "--server"],
        "cwd": ".",
        "configPath": "~/.config/nonoka/config.yaml"
      },
      "models": {
        "default": { "name": "Nonoka deepseek-chat" }
      }
    }
  },
  "permission": {
    "edit": "ask",
    "bash": "ask"
  }
}
```

## Human-in-the-loop

When a tool call matches the `hitl.dangerous_tools` list in `nonoka.yaml`,
nonoka pauses the turn and sends a `tool-approval-request` to OpenCode.
OpenCode shows the tool card with an approval dialog; after the user decides,
nonoka resumes the turn, executes approved tools, and returns the final answer.

Example `nonoka.yaml`:

```yaml
model: "deepseek-chat"

cli:
  auto_approve: false

hitl:
  policy: interactive
  dangerous_tools:
    - write_file
    - edit_file
    - delete_file
    - execute_command
```

Set `cli.auto_approve: true` (or `hitl.policy: auto`) to skip approval dialogs.

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
├── commands/        # CLI subcommands (config, opencode)
├── config/          # YAML config loading and Pydantic models
├── core/            # Orchestrator, RunnerService, SessionService, ToolService, MCPService
├── mcp/             # MCP server lifecycle manager
├── sessions/        # Session metadata persistence
├── skills/          # Skill loading and application
├── tools/           # Built-in and local tool loader
└── utils/           # Errors, logging

packages/nonoka-opencode-provider/  # TypeScript provider for OpenCode
```

## License

MIT
