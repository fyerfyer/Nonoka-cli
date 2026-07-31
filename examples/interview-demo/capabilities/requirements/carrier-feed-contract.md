# Carrier-feed reconciliation contract

1. Parse every non-blank JSONL line. Reject malformed lines with their 1-based
   source line number.
2. Normalize ISO-8601 timestamps to UTC before chronological ordering.
3. For duplicate `event_id` values, the first valid input observation wins.
4. A package starts without state and may follow only
   `CREATED -> IN_TRANSIT -> DELIVERED`.
5. Reject transitions that skip or reverse state without changing the accepted
   package state.
6. Report accepted event IDs in normalized chronological order and rejections
   in original input order.
7. CLI JSON is deterministic: sorted object keys and exactly one trailing
   newline.

Focused verifier: `NONOKA_VERIFY=focused ../.venv/bin/pytest -q`.
