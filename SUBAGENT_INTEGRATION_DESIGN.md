# Manifest-defined Sub-agent Integration Design

## Goal

Expose nonoka-agent's existing `AgentTool` capability through nonoka-cli so projects can declare bounded sub-agent roles in `.nonoka/plugin.json`. The main Agent sees those roles as tools and decides when to delegate, while users retain control over models, prompts, limits, and permissions.

This feature is intended for demonstrations and practical task decomposition. It is not a general multi-agent scheduler. It supports predeclared roles only — the model can delegate to declared agents but can never invent new agents or authority-bearing settings at runtime.

## Current State

- `nonoka-agent` already provides `AgentTool`, `MemoryStrategy`, isolated child sessions, nesting-depth protection, cancellation propagation, and result extraction.
- `AgentTool` reuses the parent session's `Runner` when available, so child sessions naturally retain the configured checkpoint store, hooks, observability pipeline, model cache, and cancellation behavior.
- nonoka-agent's `Agent` is an immutable configuration object. Sub-agent definitions should therefore be compiled into `AgentTool` capabilities when the main Agent is built.
- `.nonoka/plugin.json` already supports `AgentEntry` objects with `name`, `description`, `model`, `system_prompt`, `max_turns`, and `allowed_tools`.
- nonoka-cli currently loads and merges manifest agent entries only for system-prompt summaries. It does not convert them into executable `AgentTool` capabilities.
- `PlanningService` is currently a disabled stub, while the README still describes executable `plan_task` and `review_changes` tools.
- OpenCode may expose its own native `task` tool in normal sessions. That host-managed capability is separate from Nonoka's manifest-defined `AgentTool` roles and remains disabled in benchmark profiles.

## Recommended Model

Users declare the available roles; the main Agent autonomously chooses whether and when to call them.

```text
.nonoka/plugin.json
  -> PluginManifestLoader
  -> merge_manifests
  -> validated AgentEntry definitions
  -> child Agent configurations
  -> namespaced AgentTool capabilities
  -> main Agent tool list
```

The main Agent may delegate to declared roles, but it cannot invent a model, system prompt, tool set, or recursive agent hierarchy that was not approved by configuration.

## Manifest Example

```json
{
  "schema_version": "1.0",
  "name": "project-agents",
  "agents": [
    {
      "name": "planner",
      "description": "Produce a concise file-level implementation plan before a complex change.",
      "model": "deepseek/deepseek-v4-pro",
      "system_prompt": "Analyze the requested change and return a numbered plan, acceptance criteria, and focused verification suggestions.",
      "max_turns": 2,
      "allowed_tools": []
    },
    {
      "name": "reviewer",
      "description": "Review a proposed change against its task and identify blocking defects.",
      "model": "deepseek/deepseek-v4-pro",
      "system_prompt": "Return an approval decision, blocking issues, missing requirements, and verification gaps.",
      "output_contract": "review",
      "max_turns": 3,
      "allowed_tools": []
    }
  ]
}
```

Each role is exposed to the main Agent with a collision-resistant name such as `agent__planner` or `agent__reviewer`. The original manifest name remains available in capability metadata for logging and UI rendering.

## AgentTool Construction

The CLI should translate each validated manifest entry into the existing nonoka-agent primitives rather than implementing a second sub-agent runtime.

```python
child = (
  AgentBuilder()
  .model(entry.model)
  .system_prompt(entry.system_prompt)
  .max_turns(entry.max_turns)
  .max_steps(0)
  .metadata(role=entry.name, source="project_manifest")
  .tag("subagent", "project-defined")
  .build()
)

capability = AgentTool(
  agent=child,
  name=f"agent__{entry.name}",
  description=entry.description,
  memory_strategy=MemoryStrategy.ISOLATE,
  max_depth=1,
)
```

The exact builder calls may differ, but the first implementation must preserve isolated memory, one-level delegation, a small turn limit, and no child tools.

## Why the First Version Is Tool-free

In standalone mode, a child Agent could technically execute local Nonoka capabilities through the shared Runner. In OpenCode external-tools mode, however, host tools such as `read`, `edit`, `write`, and `bash` require a host-managed tool-call pause, approval, receipt, and resume cycle. Calling those capabilities from inside a locally executing `AgentTool` would create a nested external execution boundary that the current bridge protocol does not model.

The first version therefore passes all required evidence through the `task` and `context` arguments and gives child Agents no tools. This keeps sub-agent execution local, deterministic, permission-safe, and compatible with the existing OpenCode bridge.

`allowed_tools` remains in the manifest schema for future standalone support, but the initial implementation must reject or ignore non-empty values with a visible warning. It must never silently grant host tools to a child Agent.

## Tool Contract

All manifest-defined AgentTools use the existing `AgentTool` input contract:

```json
{
  "task": "The precise work to delegate",
  "context": "Optional repository summary, diff, acceptance criteria, or other bounded evidence"
}
```

Role-specific prompts determine the output format. The initial implementation may return structured Markdown. Typed planner or reviewer result models should be added only when a real consumer requires machine-readable fields.

## Execution Flow

```text
User task
  -> main Nonoka Agent
  -> optional agent__planner call
  -> isolated child session on the shared Runner
  -> plan returned as ordinary tool output
  -> main Agent executes OpenCode host tools
  -> main Agent runs host-attested focused verification
  -> optional agent__reviewer call with bounded diff/context
  -> reviewer findings returned as ordinary tool output
  -> main Agent repairs blocking findings
  -> completion contract evaluates workspace and verification evidence
```

Sub-agent output is advisory context. It cannot satisfy workspace mutation, focused verification, completion-contract, or official benchmark requirements by itself.

## CLI Integration Points

1. Add an `_effective_agent_entries()` helper beside `_effective_allowed_tools()` in `Orchestrator` so merged manifest roles are resolved once.
2. Pass the validated entries into `AgentFactory` during initialization and rebuilds.
3. Add a small `ProjectAgentFactory` or equivalent helper that converts `AgentEntry` objects into child `Agent` configurations and `AgentTool` capabilities.
4. Register those capabilities in both `AgentFactory.build()` and `AgentFactory.build_with_external_tools()`.
5. Include the namespaced tool names in `SystemPromptBuilder` summaries, while treating the actual tool schemas as the authoritative interface.
6. Replace or deprecate the current `PlanningService` stub. Existing `agents.planner` configuration may be translated into a synthetic manifest entry for backward compatibility, but it must require an explicitly configured model.
7. Correct the README only after deterministic tests prove the tools are executable.

## Configuration Precedence

Project manifests are the primary source for custom sub-agents. If multiple manifests declare the same role name, the existing `merge_manifests` last-definition-wins behavior applies.

If backward-compatible global roles are retained, precedence should be:

1. Project `.nonoka/plugin.json` entry.
2. Explicit global `agents.<role>` configuration.
3. No role.

The main model must not be used as an implicit fallback for enabling a sub-agent. Empty role models mean disabled.

## Runtime, Safety, and Cost Boundaries

- Manifest roles are disabled unless a model is explicitly configured.
- Role names must satisfy the same safe tool-name rules as other capabilities and are exposed under an `agent__` namespace.
- `MemoryStrategy.ISOLATE` is mandatory in the initial version.
- `max_depth=1` prevents recursive delegation.
- Child `max_turns` must be bounded to a small positive value; recommended default is three.
- Child `max_steps=0` guarantees tool-free execution.
- Child Agent metadata must include the manifest role and source path for observability.
- Parent cancellation propagates through the existing `AgentTool` implementation.
- Child sessions have independent runtime usage. The CLI must bound each child invocation because the parent's token budget is not automatically a single aggregate allowance for every child session.
- Parent-facing sub-agent output must pass through the normal tool-output policy before entering the main conversation.
- Invalid roles, duplicate effective tool names, unsupported tools, and missing models must produce explicit configuration diagnostics rather than partial registration.

## Benchmark Policy

SWE-bench and Terminal-Bench profiles should continue to disable OpenCode's native `task` tool and must also disable manifest-defined AgentTools. Benchmark adapters should set an explicit environment policy such as `NONOKA_DISABLE_PROJECT_AGENTS=1` when provisioning the bridge.

This preserves reproducibility, avoids unbounded delegation costs, and keeps benchmark attribution focused on the main Agent and official verifier.

## Dynamic Agent Creation

A generic runtime tool such as `create_subagent(model, system_prompt, tools,
max_turns)` remains intentionally forbidden. It would permit cost expansion,
permission escalation, recursive creation, unstable schemas, and irreproducible
benchmark trajectories.

The earlier `agent__spawn` compromise has been removed under the same
principle: agents can only be declared in the manifest, never invented by the
model at runtime. When a child needs workspace evidence, declare the tool
subset on the role instead:

```json
{
  "agents": [
    {
      "name": "researcher",
      "model": "deepseek/deepseek-v4-pro",
      "system_prompt": "Read the relevant files and return a concise summary.",
      "allowed_tools": ["custom__read_file", "custom__grep_files"],
      "tool_budget": 8
    }
  ]
}
```

`allowed_tools` is an authorization ceiling resolved from the parent's tool
set at invoke time. In-process capabilities (custom, internal MCP/skill, and
hosted tools) execute locally in the child session; host-native external tools
cannot be routed back from a child session, so declaring them degrades the
invocation with a structured `unsupported_sub_agent_tools` error.

## Reliable Completion

Once the completion contract is fully satisfied, the workspace-progress
extension requests a finalization turn that exposes no tools. This is a
monotonic permission restriction: extensions may remove tools for a turn but
cannot add, rewrite, or execute them. It prevents post-verification polishing
and repeated equivalent checks from converting a successful workspace into a
turn-budget failure.

Evidence-gated CLI runs interpret `maxTurns` as the work-turn budget and reserve
one additional model call for the final response. Without that reserve, a
focused verification performed on the last allowed work turn can satisfy every
acceptance criterion yet still terminate before the model can report success.

Provider selection is task-local when a shared Runner executes child sessions.
A child may temporarily select its configured model without changing the model
used by another concurrent parent or child task; the previous provider context
is restored after the child finishes or is cancelled.

## Tests

- Manifest agent entries continue to merge with last-definition-wins semantics.
- No `agent__*` tools are registered when no manifest roles are configured.
- Each valid role produces exactly one namespaced `AgentTool` with the configured model, prompt, description, and turn limit.
- Missing models and invalid names produce explicit diagnostics and do not create tools.
- Non-empty `allowed_tools` cannot grant OpenCode host tools in the initial version.
- Child Agents have no tools, isolated memory, `max_depth=1`, and bounded turns.
- Dynamic spawning exposes no model/tool/budget parameters, validates input
  lengths, enforces an aggregate per-parent cap, and rejects name collisions.
- Concurrent child sessions keep model providers task-local and restore parent
  provider state.
- Satisfied completion contracts make the next model turn tool-free.
- Planner and reviewer roles can be invoked through the shared Runner and return their result to the parent session.
- Parent cancellation and depth-limit failures return bounded errors.
- Child session usage is visible in observability records with role metadata.
- Standalone and OpenCode external-tools builds expose the same configured `agent__*` schemas.
- Benchmark policy suppresses both manifest-defined roles and OpenCode's native `task` tool.
- Existing completion-contract and external-tool receipt tests continue to pass.

## Demo Scenario

Use a small repository task with an acceptance test and a deliberate first-pass defect. Show `.nonoka/plugin.json` declaring planner and reviewer roles, the main Agent autonomously calling `agent__planner`, editing through OpenCode, running focused verification, passing the diff to `agent__reviewer`, repairing one blocking finding, and completing with a typed verification receipt.

The demo should show separate parent and child session identifiers in observability output so the delegation is visibly implemented by Nonoka rather than simulated in the prompt.

## Completion Criteria

The feature is complete when manifest-defined roles compile into bounded `AgentTool` capabilities, work in deterministic standalone and OpenCode integration tests, remain disabled by default and in benchmark profiles, appear as distinct child sessions in observability, and do not weaken host permissions or the existing verification contract.
