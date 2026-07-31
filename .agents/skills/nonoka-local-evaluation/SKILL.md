---
name: nonoka-local-evaluation
description: >-
  Run and diagnose the local nonoka-agent and nonoka-cli integration suites
  from sibling checkouts without PyPI substitution or subprocess wrappers.
  Use for local regression testing, framework/CLI integration checks, and
  optional live evaluation preparation.
---

# Nonoka Local Evaluation

This skill is for development in the sibling checkouts:

```text
/home/fyerfyer/fyerfyer/Projects/nonoka-agent
/home/fyerfyer/fyerfyer/Projects/nonoka-cli
```

Always run from the repository being tested. `nonoka-cli/pyproject.toml` points
`nonoka` at `../nonoka-agent` as an editable local dependency. Do not install or
silently fall back to the PyPI `nonoka` package.

## Environment

Use the repository's existing uv environment and a repository-local cache when
needed. Do not create a Python subprocess harness around these commands.

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-agent
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv sync --dev

cd /home/fyerfyer/fyerfyer/Projects/nonoka-cli
UV_CACHE_DIR=/tmp/nonoka-cli-uv-cache uv sync --dev
```

The CLI editable dependency must resolve to the sibling checkout:

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-cli
uv tree | rg 'nonoka v|nonoka-cli'
```

For the TypeScript OpenCode provider, use its local Bun toolchain:

```bash
cd packages/nonoka-opencode-provider
bun install
```

## Default Tests

Run deterministic tests directly with uv/pytest. These commands exercise the
actual imported modules in the current checkout and do not spawn a CLI through
`subprocess.run`.

```bash
# nonoka-agent: excludes tests marked live (real model/API calls)
cd /home/fyerfyer/fyerfyer/Projects/nonoka-agent
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv run pytest -q -m 'not live'

# nonoka-cli: unit, integration, and bridge tests
cd /home/fyerfyer/fyerfyer/Projects/nonoka-cli
UV_CACHE_DIR=/tmp/nonoka-cli-uv-cache uv run pytest -q

# OpenCode provider
cd packages/nonoka-opencode-provider
bun test
bun run build
```

Useful focused checks:

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-agent
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv run pytest tests/core/test_external_tools.py -q

cd /home/fyerfyer/fyerfyer/Projects/nonoka-cli
UV_CACHE_DIR=/tmp/nonoka-cli-uv-cache uv run pytest \
  tests/unit/bridge/test_nonoka_tools.py \
  tests/unit/bridge/test_events.py \
  tests/unit/core/test_agent_factory.py -q
```

Tests involving external tools should use fake capabilities and in-process
`Runner`/`CliRunner` objects. Assert observable memory, events, files, and
return values; do not assert private call ordering. Mark real API tests `live`
so the default suite remains repeatable.

## Agent Evaluation Datasets

The framework-owned datasets can be listed and run directly through the local
`nonoka` entry point:

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-agent
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv run python -m nonoka.ext.eval list
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv run python -m nonoka.ext.eval run \
  --dataset humaneval --model deepseek/deepseek-v4-pro --limit 20
```

Use a configured local `.env` for credentials. Never put credentials in command
arguments, manifests, test fixtures, or artifacts.

## Live Tests

Only run live tests intentionally, with the required environment loaded:

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-agent
UV_CACHE_DIR=/tmp/nonoka-agent-uv-cache uv run pytest -m live -q
```

Live model trajectories are not deterministic. Report the exact test name,
model/provider, session turn count, and captured artifact before treating a
failure as a framework regression.

## Optional Harbor / OpenCode Trial

Harbor is an external benchmark harness, not the default integration suite.
Use it only when validating the full OpenCode bridge and official verifier. The
CLI command builds fresh wheels from both local checkouts and provisions Docker
itself; do not manually install a PyPI `nonoka` wheel first.

```bash
cd /home/fyerfyer/fyerfyer/Projects/nonoka-cli
env PATH=/tmp/nonoka-harbor-resume/bin:$PATH \
  UV_CACHE_DIR=/tmp/nonoka-cli-uv-cache \
  uv run nonoka-cli benchmark terminal-bench \
  --task <task-name> \
  --artifact-dir .nonoka/eval/<run-name> \
  --cwd /tmp/<run-name> \
  --run-timeout 3600
```

Do not add `--max-turns`, `--tool-budget`, or `--timeout` unless the evaluation
explicitly requests a bounded profile. Preserve the generated artifact path;
inspect `result.json`, the trial `agent/bridge-timeline.ndjson`, provider logs,
and `verifier/reward.txt` before diagnosing a score.

When an agent-phase timeout or bridge failure occurs, stop the run, record the
exception and last timeline events, and fix the smallest reproducible layer.
Do not tune code against a single task name, path, token pattern, or model
trajectory.

## Completion Checklist

- The imported `nonoka` path is the sibling source checkout.
- Deterministic agent and CLI suites pass.
- Provider tests and TypeScript build pass when provider code changed.
- Live failures are separated from deterministic failures.
- Harbor artifacts identify whether failure occurred in the bridge, agent phase,
  or official verifier.
- `git diff --check` passes in both repositories.
