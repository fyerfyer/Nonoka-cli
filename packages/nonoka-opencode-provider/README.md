# nonoka-opencode-provider

OpenCode provider for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

This package implements the Vercel AI SDK provider protocol so that OpenCode can
use `nonoka-cli --server` as a backend. Communication happens over stdin/stdout
using newline-delimited JSON (NDJSON).

## Installation

```bash
npm install -g nonoka-opencode-provider
# or let OpenCode install it automatically on first run
```

The easiest way to set everything up is the nonoka installer:

```bash
curl -fsSL https://nonoka.dev/install.sh | bash
```

## OpenCode configuration

Add the provider to your `~/.config/opencode/opencode.json` (or run
`nonoka-cli opencode init --global`):

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
        "default": { "name": "Nonoka Default" }
      }
    }
  },
  "permission": {
    "edit": "ask",
    "bash": "ask"
  }
}
```

### Options

| Option          | Type                  | Default                      | Description                                                |
| --------------- | --------------------- | ---------------------------- | ---------------------------------------------------------- |
| `serverCommand` | `string \| string[]`  | `"nonoka-cli --server"`      | Command to spawn the nonoka-cli bridge server.             |
| `cwd`           | `string`              | `"."`                        | Working directory for the spawned server process.          |
| `configPath`    | `string`              | `"~/.config/nonoka/config.yaml"` | Path to the nonoka YAML config file, passed to `--config`. |
| `model`         | `string`              | none                         | Model identifier override sent to nonoka-cli.              |
| `env`           | `Record<string, any>` | `{}`                         | Extra environment variables for the server process.        |

## Development

```bash
# Install dependencies
bun install

# Build
bun run build

# Test
bun test
```

## License

MIT
