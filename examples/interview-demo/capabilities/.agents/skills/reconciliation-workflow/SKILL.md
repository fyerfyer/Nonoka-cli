---
name: reconciliation-workflow
description: Diagnose and repair ordered event reconciliation without losing audit evidence.
tools:
  - file: scripts/transition_tool.py:check_transition
---
Use an evidence-first workflow:

1. Retrieve the authoritative product contract before changing code.
2. Profile the real fixture; do not infer its shape from a few hand-picked lines.
3. Reproduce the failure with focused tests.
4. Separate parsing, timestamp normalization, de-duplication, and state transition checks.
5. Preserve input coordinates in every rejection so an operator can repair the feed.
6. Run the contract's focused verifier and report its exact result.

Avoid sorting raw timestamp strings: equivalent instants may have different offsets.
