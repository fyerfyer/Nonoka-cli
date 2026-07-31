# Live OpenCode TUI evaluation — 2026-07-31

## Result

A real `deepseek/deepseek-v4-pro` coding run completed successfully through:

```text
OpenCode TUI
  -> nonoka-opencode-provider@0.2.16
  -> local nonoka-cli@0.2.10 --server
  -> sibling nonoka-agent@1.3.7 source checkout
```

The run used a fresh temporary Git project at:

```text
/tmp/nonoka-live-demo-RgCfBs/project
```

Baseline verification was exactly `3 failed`. The model edited
`src/parcelwatch/reconcile.py` and `src/parcelwatch/cli.py`; independent
post-run verification was `3 passed`.

## Recorded execution

- Model: `deepseek/deepseek-v4-pro`
- Framework turns: 13 (the final turn was tool-free finalization)
- Tool calls: 25
- First visible bridge output: 4.842 seconds
- First workspace mutation: 113.229 seconds
- Wall time: 174.531 seconds
- Termination: success
- Focused verification: passed, 3 collected tests
- Workspace audit: verified

Capability evidence:

| Capability | Calls |
| --- | ---: |
| `load_skill` | 1 |
| `skill__reconciliation-workflow__check_transition` | 5 |
| `mcp__product_contract__get_reconciliation_contract` | 1 |
| `custom__profile_feed` | 1 |
| OpenCode `read` | 8 |
| OpenCode `edit` / `write` | 2 |
| OpenCode `bash` | 2 |
| OpenCode `todowrite` | 5 |

The final host receipt recorded:

```text
NONOKA_VERIFY=focused ../.venv/bin/pytest -q
3 passed in 0.02s
focused_verification=passed
```

Artifacts:

```text
/tmp/nonoka-live-demo-RgCfBs/artifacts/run2/traces/trace-20260731.jsonl
/tmp/nonoka-live-demo-RgCfBs/artifacts/run2/provider.log
/tmp/nonoka-live-demo-RgCfBs/artifacts/run2/events.db
```

## Defects found by the rehearsal

### 1. Demo Skill was copied outside the project

The prebuilt demo copied `capabilities/.agents` to the demo root, while the
OpenCode task cwd was `project/`. `SkillRegistry` correctly searches the task
project's standard `.agents/skills` directory, so the Skill was unavailable in
the first attempt.

The README materialization now copies `.agents` into `project/.agents`.
`verify_wiring.py` was run before the second attempt and invoked the MCP, Skill,
and custom tool successfully.

### 2. OpenCode native Skill was not denied for the Build agent

OpenCode 1.18 migrates `tools.skill=false` into top-level
`permission.skill=deny`, but the generated `agent.build.permission` block
overrode that value with `*=ask`. The first attempt therefore displayed a
permission prompt for OpenCode's native `skill` tool.

Generated permissions now include `skill: deny` at both levels. `opencode
debug config` confirmed:

```json
{"top_skill":"deny","build_skill":"deny","tools_skill":false}
```

### 3. Trace verification quality was incorrectly ambiguous

Operational signals returned `verification_quality=ambiguous` even though the
trace contained a passed typed receipt and a real pytest command. Two causes
were fixed:

- typed verification no longer short-circuits runner classification;
- the runner matcher accepts environment prefixes and path-qualified pytest,
  including `NONOKA_VERIFY=focused ../.venv/bin/pytest`.

The same captured trace now reports `verification_quality=runner`.

### 4. Time to first output ignored tool-first turns

The task produced its first tool call after about five seconds, but the metric
reported 174 seconds because it only considered text deltas from the final
resumed request. Bridge trace conversion now treats text, tool calls, and
approval requests as visible output and correlates resumed request IDs by
session ID. The same trace changed from 174.530 to 4.842 seconds.

## Remaining observations

- Provider usage remains empty, so token and cost metrics are still zero/null.
  This is a real P1 instrumentation gap; no estimate was fabricated.
- OpenCode changed from 1.18.9 before the rehearsal to 1.18.10 during it even
  though the project config contains `autoupdate: false`. The exact updater
  lifecycle needs a separate host-level reproduction before making a claim.
- The standalone capability verifier logged an MCP disconnect cancel-scope
  race after all three invocations succeeded. It did not affect the TUI run,
  but should receive a focused lifecycle regression test.
- LSP was disabled in the TUI. The run therefore makes no LSP-backed repo-map
  claim.
