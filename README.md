# nonoka-cli

OpenCode backend for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

`nonoka-cli` runs as a stdio NDJSON bridge server (`nonoka-cli --server`) that
implements the Vercel AI SDK provider protocol. It is consumed by the
`nonoka-opencode-provider` TypeScript package so that OpenCode can use Nonoka
agents.

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
├── config/          # YAML config loading and Pydantic models
├── core/            # Orchestrator, RunnerService, SessionService, ToolService, MCPService
├── mcp/             # MCP server lifecycle manager
├── sessions/        # Session metadata persistence
├── skills/          # Skill loading and application
├── tools/           # Built-in and local tool loader
└── utils/           # Errors, logging

packages/nonoka-opencode-provider/  # TypeScript provider for OpenCode
```

## OpenCode configuration example

Add to `~/.config/opencode/opencode.json`:

```json
{
  "providers": {
    "nonoka": {
      "model": "deepseek-chat",
      "provider": {
        "custom": {
          "baseURL": "stdio",
          "serverCommand": ["nonoka-cli", "--server"],
          "cwd": ".",
          "configPath": "./nonoka.yaml",
          "env": {
            "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"
          }
        }
      }
    }
  }
}
```

## License

MIT
