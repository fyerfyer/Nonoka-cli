# Nonoka Architecture And Next Steps

## Positioning

Nonoka is a lightweight, inspectable Agent runtime with an OpenCode terminal
experience. It is not intended to be a distributed, fully managed multi-agent
platform. The primary engineering objective is reliable, bounded, and
measurable execution for coding and tool-use tasks.

## Architecture

```text
OpenCode TUI
  <-> nonoka-opencode-provider (TypeScript, AI SDK stream adapter)
  <-> nonoka-cli stdio NDJSON bridge
  <-> nonoka-agent runtime
       - Agent / Runner / ReAct, Plan, Reflective paradigms
       - persistent SessionRuntimeState and checkpoints
       - tool, MCP, skill, HITL, and safety policies
       - execution traces, event store, metrics, and evaluation extensions
```

OpenCode owns its native tool execution, approval UI, and TUI rendering.
Nonoka owns the model decision loop and persisted session state. An OpenCode
tool call pauses the Nonoka session; OpenCode executes or obtains approval for
the call; the provider returns an attested receipt; then Nonoka resumes the
same session. This boundary avoids duplicating the terminal client or bypassing
its approval model.

Local MCP and skill tools remain Nonoka-managed capabilities. Their names are
namespaced and they follow the runtime's conservative execution policy: only
explicitly read-only calls may be run concurrently; unknown and stateful calls
are serialized.

## Existing Strengths

- Persistent runtime limits span pause/resume boundaries: model turns, tool
  calls, deadline, context, token, and cost budgets.
- Typed terminal reasons make budget exhaustion, cancellation, and deadline
  failures observable rather than appearing as normal model completions.
- Execution traces, SQLite event storage, structured logs, and optional Open
  Telemetry hooks support local debugging without requiring a hosted platform.
- Tool-output truncation and spill policies address a practical coding-agent
  failure mode: large command and file outputs consuming the context window.
- Evaluation is deliberately separated into deterministic regression, local
  framework diagnostics, paired strategy experiments, release matrices, and
  official end-to-end verifier runs.

## Deliberate Non-Goals

Do not add a general agent swarm or distributed multi-agent runtime as a
default CLI feature. For coding work, unconstrained swarms multiply model cost,
copy context, make workspace mutations conflict, and make failures difficult to
attribute.

The supported collaboration pattern is bounded delegation:

```text
Main agent: the only workspace writer and verifier
  -> planner: read-only plan
  -> repository analyst: read-only evidence
  -> reviewer: read-only diff and verification review
```

Delegates should be short-lived, non-recursive, budgeted from the parent task,
and return structured output with evidence references. Keep this optional and
retain it only if an evaluation matrix shows a net success-rate improvement
that justifies its extra latency and token cost.

Implementation notes (framework-owned, CLI only declares tools):

- Delegates run in the scheduler's read-only parallel wave. Model selection
  is context-local (`Runner.current_llm`, a ContextVar), so concurrent
  delegates on different models cannot clobber each other's provider; the
  CLI's project-agent tools are pure `read_only` and no longer force
  serialization.
- Delegates choose memory isolation per call: `ISOLATE` (empty memory),
  `INHERIT` (last N parent entries), or `CLONE` (deep copy of the parent's
  full memory — child needs complete background, parent stays clean,
  isomorphic to DeepAgents `mode: "fork"`). The old live-shared `SHARE`
  strategy was removed: bidirectionally mutable shared memory is an
  AutoGen-style pollution surface the industry has abandoned in favour of
  one-way forks. Arguments are validated by a Pydantic `AgentTask` model,
  and the parent model may override the memory strategy per invocation.
- Delegate transcripts stay in the child session; only the extracted result
  returns to the parent (see P0-2 in COMPLEX_TASK_REMEDIATION.md).
- Delegates are declared, never invented: every sub-agent role comes from a
  user-owned manifest (`.nonoka/plugin.json`), and the parent model can only
  narrow within that grant (per-call memory strategy, task/context wording)
  — it cannot widen tools, budgets, or spawn undeclared roles. The earlier
  dynamic spawn tool was removed for exactly this reason.
- Delegate tool authority is a user-granted ceiling: `allowed_tools` in the
  manifest grants a bounded, read-oriented tool subset so delegates can
  explore autonomously instead of requiring the parent to paste file
  content; an empty list keeps the delegate tool-free (the original v1
  behaviour). Host-native external tools are not yet routable from a child
  session (the bridge resume path only keys on the top-level session), so
  declaring them degrades to a structured `unsupported_sub_agent_tools`
  error instead of a silent failure. Workspace write authority is never
  granted to delegates.
- Delegate LLM usage is aggregated into the parent session's usage ledger
  (`AgentTool` rolls child consumption up on every exit path), so the
  parent's runtime limits and cost accounting always see the whole call
  tree.

## Next Priorities

### P0: Release Contract

The CLI and framework must declare and verify compatible versions. Features
such as persisted runtime limits must not silently disappear when a user has a
published framework version that predates the local checkout. Add a protocol
capability/version handshake and expose the resolved versions in `doctor`.

### P0: Small Reproducible Scorecard

For each release candidate, record a fixed manifest containing repository
revisions, model, temperature, runtime budgets, sample IDs, verifier, timeout,
and artifact locations. Report outcomes separately for:

1. deterministic tests;
2. framework diagnostic and strategy comparisons;
3. OpenCode -> CLI -> Agent official end-to-end runs.

Do not combine these into one aggregate score.

### P1: Operational Signals

Track metrics that diagnose long-running coding agents:

- time and tool calls before the first workspace mutation;
- whether a verifier runs after the last mutation;
- repeated no-progress calls;
- partial observations without a later complete observation;
- terminal-reason distribution;
- p50/p95 time to first streamed output, wall time, tokens, tool calls, and
  estimated cost.

The existing service should stay a single-node demonstration/control surface
until these local signals show a real scaling requirement. A distributed task
queue, hosted dashboard, or generic RAG layer is not currently justified.

### P1: Trust Boundary Clarity

Make `doctor` and traces distinguish OpenCode-hosted tools from locally
executed MCP/skill tools. Report the configured sandbox backend and make an
unavailable required sandbox a hard preflight failure.

## Evaluation Expansion Policy

More evaluation cases are useful, but raw count is not the goal. Expand in
predeclared, stratified slices so failures teach something:

| Lane | Initial target | What it measures |
| --- | ---: | --- |
| Deterministic regression | Every PR | Protocol, runtime, checkpoint, safety, and adapter behavior |
| Framework diagnostic | 20-30 samples per task family | Strategy quality and efficiency under a fixed model policy |
| Paired strategy comparison | Same sample IDs, 3 trials when stochastic | Whether a feature improves success enough to justify cost |
| OpenCode end-to-end | 10-20 verifier-backed tasks across categories | Bridge lifecycle, approval, receipt/resume, and tool execution |

Task families should include small code repair, multi-file edits, shell and
service work, tool-heavy inspection, recovery from a failed command, and
large-output/context-pressure cases. Preserve infrastructure-invalid and
operator-stopped runs as separate classifications; neither is a capability
score.

When an evaluation fails, first classify it as framework loop, CLI/provider
protocol, host tool execution, model behavior, benchmark infrastructure, or
verifier setup. Add a deterministic regression only for a reproducible
framework/bridge defect. Avoid task-specific prompt heuristics that inflate a
single benchmark while reducing generality.

## Continuous Test And Improvement Loop

Test expansion is a deliberate discovery process, not a count-driven exercise.
Add cases gradually to the predeclared slices above, then use the resulting
trajectories to decide whether the runtime needs a change.

For every retained failure or material inefficiency:

1. Preserve the manifest, redacted trace, tool receipts, verifier output, and
   environment classification.
2. Decide whether the primary cause is the framework, CLI/provider protocol,
   host integration, model trajectory, or benchmark infrastructure.
3. For a reproducible framework or bridge defect, add the narrowest
   deterministic regression first, then make the implementation change.
4. Re-run the fixed slice and compare success, p50/p95 latency, turns, tool
   calls, tokens, and cost against the prior artifact.

This creates a defensible data loop: test failures reveal a concrete runtime
gap, the regression prevents recurrence, and the fixed manifest measures
whether the change improves the intended task family without merely optimizing
one task. Do not add prompt rules or benchmark-specific heuristics unless a
broader, held-out slice demonstrates the expected benefit.

## Evidence And Resume Claims

Current evidence is useful but small: 513 passing agent tests, 222 passing CLI
tests, three positive official Harbor verifier cases across distinct task types,
and two sampled HumanEval diagnostic passes. These are examples, not an overall
benchmark percentage.

For the fixed 20-task sanitized-MBPP experiment, direct passed 12 tasks,
ordinary tool-assisted passed 11, and verified-repair passed 12. This is a
useful design result: verification repair should remain opt-in when a workspace
and deterministic verifier are available, rather than becoming a global
default.

Resume claims must include the evaluation scope and denominator. Prefer
"three verifier-backed end-to-end task categories" to an unsupported
"Terminal-Bench pass rate", and describe the evaluation architecture and
failure classification as engineering work in its own right.
