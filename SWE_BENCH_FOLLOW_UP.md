# SWE-bench Follow-up

## Scope

The Flash run `swe-flash-selected10-v1` resolved 6 of 10 selected SWE-bench
Lite instances. All ten patches were produced through the OpenCode -> provider
-> nonoka-cli -> nonoka-agent path, applied successfully, and graded by the
official verifier. This is a targeted regression sample, not a full Lite score.

The rejected Flask and SymPy patches show incomplete requirement coverage and
unreliable self-verification. They do not indicate a broken adapter or CLI
bridge.

## Priorities

1. Make verification results typed and trustworthy.
2. Require successful post-change verification before a coding task is called
   complete.
3. Make the TUI and saved artifacts distinguish a changed workspace from a
   verified solution.

## nonoka-agent

- Add a completion rule for a successful, host-attested verification command.
  A workspace mutation and a generic successful command are not sufficient.
- Model verification as `passed`, `failed`, `unavailable`, or `not_run`.
  Missing test runners, zero collected tests, timeouts, and truncated results
  must not satisfy the completion contract.
- Permit one bounded repair turn after a deterministic focused check fails.
- Prompt for an explicit acceptance checklist before editing, then require
  evidence for every item before completion.

## nonoka-cli

- Provide a dedicated verification command runner. It should preserve the
  command's real exit status and record the command, cwd, timeout, output
  truncation, test collection, and failure summary.
- For shell execution, prevent pipelines such as `pytest ... | tail` from
  masking the test process failure. Use `pipefail` where shell pipelines are
  unavoidable; prefer argv-based execution for verification.
- Add a coding profile that installs the completion contract for repository
  changes, rather than relying only on system-prompt instructions.
- Display and persist separate statuses: `modified`, `focused_test_passed`,
  `full_suite_passed`, `verification_failed`, and `verification_unavailable`.
- Relax the OpenCode coding prompt instruction that discourages directory
  exploration. Agents should inspect the smallest relevant part of a repository
  when implementing a requested change.

## OpenCode provider / adapter

- Return structured receipts for native tool execution, including `exit_code`,
  `timed_out`, `truncated`, command, and cwd.
- Forward typed verification receipts into nonoka-agent trace and completion
  state; do not require the agent to infer success from text output.
- Keep the existing adapter flow and workspace-effect tracking. No architectural
  rewrite is indicated by this evaluation.

## Evaluation and Demo

- Re-run the same ten instances after the verification changes. Classify every
  outcome as bridge failure, environment failure, masked verification,
  incorrect patch, timeout, or official-verifier rejection.
- Then run a stratified 20-30 instance sample and compare selected failures with
  native OpenCode using the same model and task environment.
- For a project demonstration, show an existing-repository change through
  inspection, edit, focused test, and diff; expose the structured verification
  result in the TUI and saved run artifact.
