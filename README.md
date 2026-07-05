# nonoka-cli

OpenCode backend for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

`nonoka-cli` runs as a stdio NDJSON bridge server (`nonoka-cli --server`) that
talks to the `nonoka-opencode-provider` TypeScript package. OpenCode uses the
provider to drive Nonoka agents, with full support for tool cards and
human-in-the-loop (HITL) approval.

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

> **Note on OpenCode 1.17.13**: nonoka sends `tool-approval-request` stream
> parts to the OpenCode provider, but the current OpenCode release does not
> render an approval dialog for tools emitted by custom npm providers. Until
> this is resolved on the OpenCode side, the practical fallback is to enable
> auto-approval.

Set `cli.auto_approve: true` (or `hitl.policy: auto`) to run tools without
manual approval:

```yaml
model: "deepseek-chat"

cli:
  auto_approve: true

hitl:
  policy: auto
  dangerous_tools:
    - write_file
    - edit_file
    - delete_file
    - execute_command
```

For users who need explicit approval per tool, the planned next step is to
expose nonoka's tools through an MCP server so that OpenCode treats them as
first-class tools and applies its built-in permission / HITL system. See
`DESIGN.md` section 13 for the MCP integration design.

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
├── core/            # Orchestrator, RunnerService, SessionService, ToolService, MCPService
├── mcp/             # MCP server lifecycle manager
├── sessions/        # Session metadata persistence
├── skills/          # Skill loading and application
├── tools/           # Built-in and local tool loader
└── utils/           # Errors, logging

packages/nonoka-opencode-provider/  # TypeScript provider for OpenCode
install.sh                          # One-line installer
```

## License and attribution

`nonoka-cli` and `nonoka-opencode-provider` are released under the MIT License.

The terminal TUI and OpenCode client/server architecture are provided by
[OpenCode](https://github.com/anomalyco/opencode) (MIT License). The agent
core is provided by the [Nonoka](https://pypi.org/project/nonoka/) framework.

See `LICENSE` and `NOTICE` for full details.
