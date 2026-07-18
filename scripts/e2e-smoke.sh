#!/usr/bin/env bash
#
# End-to-end smoke test for nonoka-cli + OpenCode.
#
# Creates an isolated venv, installs nonoka-cli from PyPI (or local wheels),
# initializes config, and runs a short LLM-backed task.
#
# Usage:
#   scripts/e2e-smoke.sh
#   scripts/e2e-smoke.sh --local-dist /path/to/dist
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_DIST_DIR=""
AGENT_DIST_DIR=""

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$1" >&2; }
error() { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --local-dist)
      LOCAL_DIST_DIR="${2:-}"
      shift 2
      ;;
    --local-dist-agent)
      AGENT_DIST_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# If --local-dist-agent is not given, default to the sibling nonoka-agent repo.
if [ -n "$LOCAL_DIST_DIR" ] && [ -z "$AGENT_DIST_DIR" ]; then
  if [ -d "$REPO_ROOT/../nonoka-agent/dist" ]; then
    AGENT_DIST_DIR="$REPO_ROOT/../nonoka-agent/dist"
  fi
fi

TEST_DIR="$(mktemp -d -t nonoka-e2e-smoke-XXXXXX)"
trap 'rm -rf "$TEST_DIR"' EXIT

info "Test directory: $TEST_DIR"

cd "$TEST_DIR"
uv venv --python python3.10 --seed .venv
# shellcheck source=/dev/null
. .venv/bin/activate

if [ -n "$LOCAL_DIST_DIR" ]; then
  info "Installing from local wheels..."
  if [ -z "$AGENT_DIST_DIR" ]; then
    error "Could not find nonoka-agent wheels. Use --local-dist-agent to specify them."
    exit 1
  fi
  # shellcheck disable=SC2086
  uv pip install $AGENT_DIST_DIR/nonoka-*.whl $LOCAL_DIST_DIR/nonoka_cli-*.whl
else
  info "Installing nonoka-cli from PyPI..."
  uv pip install nonoka-cli
fi

info "Initializing nonoka config..."
./.venv/bin/nonoka-cli config init --yes --model deepseek-chat --config "$TEST_DIR/nonoka.yaml"

info "Initializing OpenCode config..."
./.venv/bin/nonoka-cli opencode init --cwd "$TEST_DIR" --config "$TEST_DIR/nonoka.yaml" --yes

# Point serverCommand at the isolated venv binary.
python3 - "$TEST_DIR" <<'PY'
import json, sys
from pathlib import Path
test_dir = Path(sys.argv[1])
p = test_dir / "opencode.json"
data = json.loads(p.read_text())
data["provider"]["nonoka"]["options"]["serverCommand"] = [
    str(test_dir / ".venv" / "bin" / "nonoka-cli"),
    "--server",
    "--config",
    str(test_dir / "nonoka.yaml"),
]
p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY

info "Running doctor..."
./.venv/bin/nonoka-cli doctor --config "$TEST_DIR/nonoka.yaml"

# Load the same .env files nonoka-cli loads so the API-key check is accurate.
if [ -f "$HOME/.config/nonoka/.env" ]; then
  # shellcheck source=/dev/null
  . "$HOME/.config/nonoka/.env"
fi
if [ -f "$TEST_DIR/.env" ]; then
  # shellcheck source=/dev/null
  . "$TEST_DIR/.env"
fi

if [ -z "${DEEPSEEK_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
  warn "No API key set; skipping LLM smoke test."
  exit 0
fi

info "Running doctor with LLM ping..."
./.venv/bin/nonoka-cli doctor --config "$TEST_DIR/nonoka.yaml" --check-llm

info "Running one-shot task..."
./.venv/bin/nonoka run --message "Create a hello.py file that prints 'Hello from nonoka e2e smoke test!' and run it."

info "E2E smoke test passed."
