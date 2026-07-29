# nonoka-opencode-provider

OpenCode provider for the [Nonoka](https://pypi.org/project/nonoka/) Agent framework.

This package implements the Vercel AI SDK provider protocol so that OpenCode can
use `nonoka-cli --server` as a backend. Communication happens over stdin/stdout
using newline-delimited JSON (NDJSON). The provider requires a compatible bridge
protocol acknowledgement before it accepts streamed model output, so update the
provider and `nonoka-cli` together.

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
| `hostShellEnv`  | `Record<string, string>` | `{}`                      | Environment variables applied to OpenCode-hosted shell commands. |
| `hostShellInit` | `string[]`            | `[]`                         | Trusted initialization statements run before each hosted shell command. |

`env` and `hostShellEnv` intentionally have different scopes. The former configures only the `nonoka-cli --server` process. Use `hostShellEnv` or `hostShellInit` when project commands must run in a managed environment while the bridge remains on its own runtime. For example:

```json
{
  "hostShellEnv": {
    "TASK_MODE": "benchmark"
  },
  "hostShellInit": [
    "source /opt/miniconda3/etc/profile.d/conda.sh",
    "conda activate testbed"
  ]
}
```

Initialization statements come from trusted provider configuration, never from model output. A failed initialization stops the hosted command and is reported through the same exit-code receipt as the project command.

## Development

```bash
# Install dependencies
bun install

# Build
bun run build

# Test
bun test
```

## Notes

- The provider streams `tool_call`/`tool_result` events as observations so OpenCode can render tool cards. OpenCode remains the executor and approval owner for hosted tools; Nonoka receives structured execution receipts.
- Text deltas are buffered so that leading spaces are not dropped between
  chunks, fixing rendering issues like `project.Seems`.
- On OpenCode 1.17.13, `tool-approval-request` parts from custom npm providers
  do not yet render an approval dialog. Use `cli.auto_approve: true` in
  `nonoka.yaml` as a fallback, or follow the MCP server roadmap in the main
  `DESIGN.md`.

## License

MIT
