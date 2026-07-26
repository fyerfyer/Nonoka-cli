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

After installing, configure your API key and run `nonoka`:

```bash
# Interactive: it will ask for your key and save it to ~/.config/nonoka/.env
nonoka-cli config init

# Or set it manually
export DEEPSEEK_API_KEY=<your-key>

nonoka-cli doctor
nonoka
```

`nonoka` is a shortcut for `nonoka-cli run` and starts the OpenCode TUI with the
Nonoka backend. You can also use `nonoka run --message "<task>"` for one-shot
CLI usage.

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
nonoka-cli config init --yes --model deepseek/deepseek-v4-pro
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
nonoka
```

## `nonoka-cli doctor`

Diagnose your installation and configuration:

```bash
nonoka-cli doctor
```

Example output:

```
nonoka-cli doctor
✓ nonoka-cli 0.2.5
✓ Python 3.11
✓ opencode 1.18.2
✓ provider nonoka-opencode-provider@0.2.12
✓ config ~/.config/nonoka/config.yaml
✓ API key DEEPSEEK_API_KEY set
✓ OpenCode provider config in /home/user/.config/opencode/opencode.json
```

If anything is wrong, `doctor` prints a remedy line. To also verify the LLM API
key with a real (small) call, use:

```bash
nonoka-cli doctor --check-llm
```

Verify the Docker-backed command sandbox separately:

```bash
nonoka-cli doctor --check-sandbox
```

## Execution Observability

Every local runner session writes credential-redacted structured events to
`~/.local/share/nonoka/events.db`. Events include LLM prompts/responses,
tool I/O, errors, and LiteLLM token/cost usage. Inspect them without opening
the database directly:

```bash
nonoka-cli sessions list
nonoka-cli sessions show <session-id>
nonoka-cli logs --session-id <session-id>
nonoka-cli logs --json
```

The framework exposes a provider-neutral `TelemetryExporter` protocol and
`ObservabilityPipeline`; downstream applications can add Langfuse, OTLP, or
other exporters without coupling `Runner` to a vendor SDK. Export failures are
best-effort and never interrupt an agent run.

## Service Deployment

`nonoka-agent` includes an authenticated FastAPI application with `/run`,
`/chat`, `/tasks`, `/health`, and `/metrics`. Set a bearer token before
starting it:

```bash
export NONOKA_API_TOKEN="replace-with-a-long-random-token"
uv run uvicorn nonoka.server.app:create_app --factory --host 0.0.0.0 --port 8000
```

For a containerized deployment, copy `.env.example` to `.env`, set
`NONOKA_API_TOKEN`, then run `docker compose up --build`. Compose defaults to
PostgreSQL for persisted events; local development stays on SQLite. The service
is non-root, read-only, drops Linux capabilities, and does not mount the host
Docker socket.

## Agent evaluation

`nonoka-cli eval` is a thin frontend for the framework-owned benchmark engine. Its
scored built-ins are the open HumanEval and MBPP datasets. The bundled
`tool_use` suite is deliberately labelled as deterministic smoke/regression
coverage; it is not presented as a substitute for an open benchmark. Results
are stored under `.nonoka/eval/` in the current project and can be compared
locally:

```bash
nonoka-cli eval list
nonoka-cli eval run --dataset humaneval --model deepseek/deepseek-v4-pro --limit 20
nonoka-cli eval run --dataset mbpp --model deepseek/deepseek-v4-pro --limit 20
nonoka-cli eval leaderboard
```

Each built-in run records a normal Nonoka agent and a same-model direct
baseline, including pass@1, turns, tool calls, token usage, wall time, and the
agent lift. Live model calls are opt-in and are never part of the default test
suite.

For a release comparison, create a manifest before incurring any model cost.
It pins the model policy and includes HumanEval, MBPP sanitized, EvalPlus,
τ³ retail/airline, and Terminal-Bench. EvalPlus runs in a separate environment
because it owns the official strengthened verifier:

```bash
nonoka-cli eval matrix plan --model deepseek/deepseek-v4-pro --output .nonoka/eval/release-matrix.json
export NONOKA_EVALPLUS_PYTHON=/path/to/evalplus-python
nonoka-cli eval matrix run --manifest .nonoka/eval/release-matrix.json --include evalplus-humaneval
```

For complex agent tasks, the framework delegates to official harnesses instead
of reimplementing their verifiers. τ³-bench (the `tau2-bench` package) provides
multi-turn customer-service tasks with policies, simulated users, environment
tools, and action-level reward; it runs from an isolated Python 3.12
environment because of its dependency pins:

```bash
export NONOKA_TAU2_PYTHON=/path/to/tau2-python
nonoka-cli eval external run --benchmark tau2-bench --model deepseek/deepseek-v4-pro --domain retail --limit 10
```

For framework-only terminal-agent tasks, Terminal-Bench 2 delegates Docker
lifecycle and verification to Harbor. The OpenCode bridge has its own
reproducible benchmark command, so a framework score is never presented as a
CLI bridge score. Install Harbor plus the local framework/CLI checkouts in an
isolated environment, then verify prerequisites before starting a live run:

```bash
uv venv .venv-bench --python 3.13
uv pip install --python .venv-bench/bin/python -e ../nonoka-agent -e . harbor
export NONOKA_HARBOR_BIN="$PWD/.venv-bench/bin/harbor"
nonoka-cli doctor --check-benchmarks
nonoka-cli benchmark smoke --model deepseek/deepseek-v4-pro
nonoka-cli benchmark terminal-bench --model deepseek/deepseek-v4-pro
```

The Terminal-Bench command builds fresh local wheels, stages the built OpenCode
provider plus its dependencies, and copies verified host OpenCode and Python
3.13 runtime artifacts into every Harbor task container. The runtime is also
registered in uv's conventional managed-Python directory, so both the non-root
agent and official verifier scripts can reuse it without downloading another
interpreter. This ensures the
official verifier observes OpenCode using the current nonoka bridge against the task
filesystem—not a host-side shell. To verify provisioning before spending model
tokens, run one pinned task first:

```bash
nonoka-cli benchmark terminal-bench --task regex-log --install-only
```

The adapter also exposes the staged `uv` as `/root/.local/bin/uv` and `uvx`.
Verifier scripts that replace that binary still discover the registered Python
runtime. This does not preinstall task-specific test packages, data, or solution
artifacts.

Live benchmark runs do not impose cumulative model-turn, tool-call, or
per-model-call limits by default. The one-hour process watchdog only protects
against a lost or permanently stuck agent process. Use `--max-turns`,
`--tool-budget`, or `--timeout` when a deliberately bounded profile is needed.

Harbor receives the model credential through its `${DEEPSEEK_API_KEY}`
environment template. The value is never placed in the benchmark manifest or
artifact directory.

Each bridge run writes a credential-redacted manifest, OpenCode JSON events,
provider/bridge traces, and a reference to the official Harbor job directory
under `.nonoka/eval/opencode/`. Docker access is required. SWE-bench remains
an external harness because its official local evaluation requires substantially
more host resources.

Reference end-to-end validation with `deepseek/deepseek-v4-pro` against the
pinned Terminal-Bench 2 revision (`69671fba`) has earned official Harbor reward
`1` on three distinct tasks: `sanitize-git-repo`, `configure-git-webserver`, and
`break-filter-js-from-html`. The latest bridge-hardening rerun of
`break-filter-js-from-html` completed without an exception in 10m21s. These are
single-trial engineering checks, not a statistically representative leaderboard
score.

`benchmark smoke` pins OpenCode to the checkout's built provider with a local
`file:` dependency and temporarily writes an isolated `opencode.json` in the
benchmark workspace. Use a clean `--cwd` (or pass `--provider-source`) so it
never overrides an existing project OpenCode configuration.

## Configuration

### `nonoka-cli config init`

Interactive wizard that writes `~/.config/nonoka/config.yaml`. It asks for a
model identifier (e.g. `deepseek/deepseek-v4-pro`, `openai/gpt-4o`, `ollama/llama3.3`), a
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

### Cost controls and response cache

The runner keeps a local SQLite exact-response cache by default. It only stores
complete responses without tool calls, and its key includes the model, complete
message history, tool schema, generation settings, and workspace namespace.

Semantic reuse is opt-in because it has a stricter correctness contract. It
uses an OpenAI-compatible embedding endpoint only for deterministic, tool-free,
single-turn requests in a Git worktree. The cache scope is recomputed before
each completion from `HEAD`, tracked/untracked changes, the repo-map index,
system prompt, and workspace path. A write made earlier in the same OpenCode
session therefore invalidates semantic candidates immediately. Non-Git
workspaces, tool calls, and multi-turn conversations fall back to the model.

```yaml
cache:
  enabled: true
  path: ~/.cache/nonoka/llm-cache.sqlite3
  ttl_seconds: 604800
  semantic_enabled: true
  embedding_model: qwen3.7-text-embedding
  embedding_api_base: https://your-endpoint/compatible-mode/v1
  embedding_api_key_env: DASHSCOPE_API_KEY
  embedding_dimensions: 1024
  semantic_threshold: 0.92

budget:
  max_total_tokens: 120000
  max_cost_usd: 5.0
  fail_on_unknown_cost: true
```

Keep the embedding credential in the named environment variable or
`~/.config/nonoka/.env`, never in `config.yaml`. A semantic hit is recorded as
saved usage rather than actual spend; the event store exposes cache source and
similarity score without storing raw cache queries. `max_total_tokens` and
`max_cost_usd` are hard limits persisted with the task session; when price data
is unavailable, `fail_on_unknown_cost: true` terminates the task rather than
silently exceeding the cost budget.

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
        "default": { "name": "Nonoka deepseek-v4-pro" }
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
  },
  "tools": {
    "skill": false
  }
}
```

The `"tools": {"skill": false}` line disables OpenCode's native `skill:<name>`
tool so it does not collide with nonoka's `load_skill` / `skill__<name>__<tool>`
workflow. `nonoka-cli opencode init` writes this automatically.

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

`nonoka-cli`'s own `cli.auto_approve` and `hitl.policy` settings only apply to
standalone server / CLI mode. In OpenCode mode, permissions are governed by the
`permission` block in `opencode.json`. To keep `nonoka.yaml` as the single
source of truth, add a `permissions` block and re-run `nonoka-cli opencode init`:

```yaml
permissions:
  read: allow
  bash: ask
  write: ask
  edit: ask
```

`cli.auto_approve: true` still auto-allows the core coding tools when no
`permissions` block is present. For standalone mode, use `hitl.policy: auto`.

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
model: deepseek/deepseek-v4-pro

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

### Skill tool import paths

A skill file lists its tools with an `import` entry in YAML frontmatter:

```yaml
---
name: greet
description: A simple greeting skill.
tools:
  - import: greet_tool:say_hello
---
When loaded, use the say_hello tool to greet the user by name.
```

`greet_tool:say_hello` is resolved by Python's normal import machinery, so the
module must be importable from the project working directory (or from a directory
on `PYTHONPATH`). Place the tool module next to your skill file or add the skill
source directory to `PYTHONPATH` if you use a nested layout.

### Avoiding OpenCode's native `skill` tool

OpenCode has its own `skill:<name>` syntax that conflicts with nonoka's
`skill__<name>__<tool>` namespace and `load_skill` tool. The generated
`opencode.json` disables the native skill tool with `"tools": {"skill": false}`.
If you hand-write `opencode.json`, keep that setting so the model only uses
nonoka-managed skills.

## Git safety net

`nonoka-cli` can automatically create a git checkpoint before each file change
and roll back on failure. Enable it in `nonoka.yaml`:

```yaml
git:
  auto_checkpoint: true
  auto_commit: true
```

When enabled, the model can call `git_checkpoint` before dangerous operations
and `git_rollback` to restore the last checkpoint if something goes wrong.

## Repo map

For large repositories, `nonoka-cli` builds a lightweight symbol index and
injects a repo map into the system prompt. This reduces blind file reads by
giving the model a structural overview of classes, functions, and exports.

Configure it in `nonoka.yaml`:

```yaml
repo_map:
  enabled: true
  max_tokens: 4000
```

The `build_repo_map` and `search_repo_map` tools let the model refresh or
query the index on demand.

## Sub-agent workflow

`nonoka-cli` can optionally expose two sub-agent tools that the main agent can
call for complex tasks:

- `plan_task` — delegates planning to a dedicated **planner** agent and returns a
  numbered, file-level execution plan.
- `review_changes` — delegates final review to a dedicated **reviewer** agent and
  returns a structured review with issues, suggestions, and an approval flag.

Both are disabled by default. Enable them by setting their model in `nonoka.yaml`:

```yaml
model: deepseek/deepseek-v4-pro
max_turns: 20

agents:
  planner:
    model: deepseek/deepseek-v4-pro
    system_prompt: "You are a planning agent..."
  reviewer:
    model: deepseek/deepseek-v4-pro
    system_prompt: "You are a senior code reviewer..."
```

`max_turns` at the top level controls the main executor agent. The
`planner`/`reviewer` roles each have their own `max_turns` inside
`agents.<role>`.

When enabled, these tools are injected into the main agent's tool list
alongside OpenCode's native tools. The main agent decides when to call them;
the planner/reviewer run inside their own short-lived `nonoka` agent invocation
and return their results as tool output.

`review_changes` accepts an optional `files` argument. When the main agent
passes file paths, the reviewer reads those files and prepends their contents
to the review context automatically:

```yaml
# In the conversation the model can call:
# review_changes({
#   "task": "Review the changes against the goal: add logging",
#   "context": "<diff or summary>",
#   "files": ["src/main.py", "src/utils.py"]
# })
```

> **Note:** `plan_task` and `review_changes` used to live in `nonoka-agent`. They
> have been moved to `nonoka-cli` so that sub-agent configuration (model, system
> prompt, max turns) is controlled by the CLI config and can use different
> models from the main agent.

## Plugin manifest

Projects can declare their own Nonoka plugins via `.nonoka/plugin.json`:

```json
{
  "name": "my-plugin",
  "skills": ["code-review"],
  "agents": {
    "planner": { "system_prompt": "..." }
  },
  "mcpServers": {},
  "allowedTools": ["read", "edit", "bash"]
}
```

The manifest is merged with user-level config and converted into OpenCode's
skill/permission format when `nonoka-cli opencode init` runs. See
`.nonoka/plugin.json.example` for a full example.

## Known limitations

These are current behaviors observed with OpenCode CLI 1.17.18. They are tracked
here because they affect the TUI/HITL experience but cannot be fixed inside
`nonoka-cli` or `nonoka-opencode-provider`.

- [x] **OpenCode native `skill` tool conflicts with nonoka skills**: the
  generated `opencode.json` disables it with `"tools": {"skill": false}`, and
  the adapter prompt tells the model to use only `load_skill` and
  `skill__<name>__<tool>`.
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
│                    # MCPService, AgentFactory, prompt/context/task-state/output pruning,
│                    # git safety net, repo map, planning, and plugin manifest
│                    #   agent_factory.py              # Build nonoka Agent from CLI config
│                    #   prompt_builder.py             # System prompt assembly for OpenCode mode
│                    #   context_trimmer.py            # Turn-based context window trimming
│                    #   task_state.py                 # Local TODO state mirror
│                    #   tool_output_policy.py         # Tool output pruning / spill policy
│                    #   git_service.py                # Git checkpoint / rollback helpers
│                    #   repo_map_service.py           # Symbol index generation and search
│                    #   planning_service.py           # Planner sub-agent (AgentTool)
│                    #   review_service.py             # Reviewer sub-agent (AgentTool)
│                    #   plugin_manifest.py            # .nonoka/plugin.json loader
│                    #   plugin_manifest_converter.py  # OpenCode skill/permission conversion
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
