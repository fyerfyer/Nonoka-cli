You are debugging ParcelWatch's carrier-feed reconciliation. Use every configured
Nonoka capability at least once so this run also serves as an integration audit:

1. load the `reconciliation-workflow` skill and use its transition-check tool;
2. retrieve the `carrier-feed` contract from the `product_contract` MCP server;
3. profile `fixtures/carrier_feed.jsonl` with the configured custom tool.

Then inspect the repository, fix the reconciliation implementation, and add or
adjust focused tests where needed. Do not discard the first valid observation for
a duplicate event id, preserve source coordinates, and keep CLI output deterministic.
Do not merely explain or propose a patch: edit the workspace and finish by running
`NONOKA_VERIFY=focused ../.venv/bin/pytest -q` from the project root. Summarize the
root cause, changed behavior, and exact verification result.
