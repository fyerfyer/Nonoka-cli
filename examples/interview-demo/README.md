# Nonoka interview demo

For the presenter-operated Chinese walkthrough—from GitHub installation,
through natural-language MCP/Skill/custom-tool setup, to the final bug fix—see
[INTERVIEW_RUNBOOK.zh-CN.md](INTERVIEW_RUNBOOK.zh-CN.md).

This demo is a deterministic, resettable coding task that exercises the full
OpenCode bridge rather than a toy chat:

- a Nonoka-managed Skill supplies the investigation workflow and a transition
  validation tool;
- a local MCP server supplies the product contract;
- a custom Python tool profiles the deliberately tricky JSONL fixture;
- OpenCode supplies the normal read/edit/bash/todowrite coding tools;
- pytest is the final acceptance check.

## Prepare from the real installer

Use the repository's top-level `install.sh`; this example does not provide a
second installer. The following is an isolated rehearsal (replace the checkout
path if necessary):

```bash
export NONOKA_CLI_ROOT=/home/fyerfyer/fyerfyer/Projects/nonoka-cli
export NONOKA_DEMO_ROOT=/tmp/nonoka-interview-demo
uv venv "$NONOKA_DEMO_ROOT/.venv" --python 3.13
mkdir -p "$NONOKA_DEMO_ROOT/home" "$NONOKA_DEMO_ROOT/project"
cp -R "$NONOKA_CLI_ROOT/examples/interview-demo/project/." "$NONOKA_DEMO_ROOT/project/"

cd "$NONOKA_DEMO_ROOT/project"
env HOME="$NONOKA_DEMO_ROOT/home" \
  VIRTUAL_ENV="$NONOKA_DEMO_ROOT/.venv" \
  PATH="$NONOKA_DEMO_ROOT/.venv/bin:$PATH" \
  bash "$NONOKA_CLI_ROOT/install.sh" --dev --uv --yes --no-opencode
```

After the clean-install check, copy the capability samples from `capabilities/`
into the demo root, use `nonoka.yaml.in` as the explicit demo config (replace
the two placeholders), and rerun `nonoka-cli opencode init`. This separation is
intentional: `install.sh` is tested exactly as users receive it, while the
interview-only capabilities remain project fixtures rather than installer
policy.

The exact materialization commands are:

```bash
cp -R "$NONOKA_CLI_ROOT/examples/interview-demo/capabilities/mcp" "$NONOKA_DEMO_ROOT/"
cp -R "$NONOKA_CLI_ROOT/examples/interview-demo/capabilities/requirements" "$NONOKA_DEMO_ROOT/"
cp -R "$NONOKA_CLI_ROOT/examples/interview-demo/capabilities/tools" "$NONOKA_DEMO_ROOT/"
cp "$NONOKA_CLI_ROOT/examples/interview-demo/capabilities/verify_wiring.py" "$NONOKA_DEMO_ROOT/"
mkdir -p "$NONOKA_DEMO_ROOT/project/.agents"
cp -R "$NONOKA_CLI_ROOT/examples/interview-demo/capabilities/.agents/." \
  "$NONOKA_DEMO_ROOT/project/.agents/"
cp "$NONOKA_CLI_ROOT/examples/interview-demo/PROMPT.md" "$NONOKA_DEMO_ROOT/PROMPT.md"

sed \
  -e "s|__DEMO_ROOT__|$NONOKA_DEMO_ROOT|g" \
  -e "s|__PYTHON__|$NONOKA_DEMO_ROOT/.venv/bin/python|g" \
  "$NONOKA_CLI_ROOT/examples/interview-demo/nonoka.yaml.in" \
  > "$NONOKA_DEMO_ROOT/nonoka.yaml"
sed -e "s|__DEMO_ROOT__|$NONOKA_DEMO_ROOT|g" \
  "$NONOKA_CLI_ROOT/examples/interview-demo/run.sh.in" \
  > "$NONOKA_DEMO_ROOT/run.sh"
sed -e "s|__DEMO_ROOT__|$NONOKA_DEMO_ROOT|g" \
  "$NONOKA_CLI_ROOT/examples/interview-demo/verify-wiring.sh.in" \
  > "$NONOKA_DEMO_ROOT/verify-wiring.sh"
chmod +x "$NONOKA_DEMO_ROOT/run.sh" "$NONOKA_DEMO_ROOT/verify-wiring.sh"

uv pip install --python "$NONOKA_DEMO_ROOT/.venv/bin/python" \
  -e "$NONOKA_DEMO_ROOT/project"
"$NONOKA_DEMO_ROOT/.venv/bin/nonoka-cli" opencode init \
  --config "$NONOKA_DEMO_ROOT/nonoka.yaml" \
  --cwd "$NONOKA_DEMO_ROOT/project" --yes
"$NONOKA_DEMO_ROOT/.venv/bin/nonoka-cli" doctor \
  --config "$NONOKA_DEMO_ROOT/nonoka.yaml"

git -C "$NONOKA_DEMO_ROOT/project" init -q
git -C "$NONOKA_DEMO_ROOT/project" add .
git -C "$NONOKA_DEMO_ROOT/project" \
  -c user.name='Nonoka Demo' -c user.email='demo@nonoka.local' \
  commit -qm 'interview demo baseline'
```

Preflight should report `Capability invocation OK` and then exactly three
intentional test failures:

```bash
cd "$NONOKA_DEMO_ROOT"
./verify-wiring.sh
cd project
../.venv/bin/pytest -q  # expected before the agent: 3 failed
```

No credential is copied into the demo. `run.sh` loads
`~/.config/nonoka/.env` when it exists, or you can export the model key first.

## Rehearse

Terminal 1:

```bash
cd /tmp/nonoka-interview-demo
./run.sh
```

Paste `PROMPT.md` into the TUI. The expected visible sequence is:

1. TODO plan;
2. `load_skill` and `skill__reconciliation-workflow__check_transition`;
3. `mcp__product_contract__get_reconciliation_contract`;
4. `custom__profile_feed`;
5. source/test inspection, edits, and focused pytest;
6. a concise completion summary.

To restore the starting bug after any rehearsal:

```bash
cd /tmp/nonoka-interview-demo/project
git restore .
```

The demo task only edits tracked source/tests, so `git restore .` is sufficient
and does not delete unrelated files.

## Interview pacing

- **90 seconds:** architecture and `nonoka.yaml`.
- **5–8 minutes:** run the prepared task and narrate tool ownership in the TUI.
- **2 minutes:** show `git diff`, test results, and bridge evidence.
- **Fallback:** if the model API or venue network is unavailable, use
  `./verify-wiring.sh` plus a transcript captured during rehearsal. The wiring
  check starts and invokes the MCP server, Skill tool, and custom tool through
  the real external-tools Agent without making an LLM call.

The demo is intentionally scoped to one domain workflow. A small, reliable
task with three capability boundaries is more persuasive than an open-ended
repository rewrite that may not finish during an interview.
