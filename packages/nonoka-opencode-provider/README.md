# nonoka-opencode-provider

OpenCode provider for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

This package implements the Vercel AI SDK provider protocol so that OpenCode can
use `nonoka-cli --server` as a backend. Communication happens over stdin/stdout
using newline-delimited JSON (NDJSON).

## Installation

```bash
npm install nonoka-opencode-provider
# or
bun add nonoka-opencode-provider
```

## OpenCode configuration

Add the provider to your `~/.config/opencode/opencode.json`:

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

### Options

| Option          | Type                  | Default               | Description                                                                   |
| --------------- | --------------------- | --------------------- | ----------------------------------------------------------------------------- |
| `serverCommand` | `string[]`            | `["nonoka-cli", "--server"]` | Command and arguments to spawn the nonoka-cli bridge server.           |
| `cwd`           | `string`              | `"."`                 | Working directory for the spawned server process.                             |
| `configPath`    | `string`              | `"./nonoka.yaml"`     | Path to the nonoka YAML config file, passed to `--config`.                    |
| `model`         | `string`              | `"deepseek-chat"`     | Model identifier sent to nonoka-cli and reported to OpenCode.                 |
| `env`           | `Record<string, any>` | `{}`                  | Extra environment variables for the server process.                           |

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
