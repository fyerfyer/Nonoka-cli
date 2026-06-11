# nonoka-cli

Terminal frontend for the [Nonoka](https://pypi.org/project/nonoka) Agent framework.

## Quick Start

```bash
# Install
pip install nonoka-cli

# Start interactive REPL
nonoka-cli

# With custom config
nonoka-cli --config ~/.config/nonoka/config.yaml

# Override model
nonoka-cli --model gpt-4o
```

## Configuration

Create `~/.config/nonoka/config.yaml`:

```yaml
model: "gpt-4o"
system_prompt: |
  You are a helpful AI assistant.
```

> The default config file is `nonoka.yaml`.

## Commands

- `/exit` — Quit the CLI
- `/new` — Start a new session
- `/help` — Show help

## Development

```bash
# Setup
uv venv
uv pip install -e ".[dev]"

# Run tests
pytest
```
