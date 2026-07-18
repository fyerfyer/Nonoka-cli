#!/usr/bin/env bash
#
# Release script for nonoka + nonoka-cli + nonoka-opencode-provider.
#
# Usage:
#   scripts/release.sh [OPTIONS]
#
# Options:
#   --dry-run          Build and run local smoke tests, but do not publish.
#   --skip-pypi        Skip uploading Python packages to PyPI.
#   --skip-npm         Skip publishing the npm provider package.
#   --pypi-repository  PyPI repository URL (default: pypi).
#                      Use "testpypi" to publish to TestPyPI.
#
# Environment:
#   NONOKA_AGENT_ROOT  Path to nonoka-agent repo (default: ../nonoka-agent).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ROOT="${NONOKA_AGENT_ROOT:-$REPO_ROOT/../nonoka-agent}"
PROVIDER_ROOT="$REPO_ROOT/packages/nonoka-opencode-provider"

DRY_RUN=false
SKIP_PYPI=false
SKIP_NPM=false
PYPI_REPOSITORY="pypi"

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$1" >&2; }
error() { printf '\033[1;31m==>\033[0m %s\n' "$1" >&2; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --skip-pypi)
      SKIP_PYPI=true
      shift
      ;;
    --skip-npm)
      SKIP_NPM=true
      shift
      ;;
    --pypi-repository)
      PYPI_REPOSITORY="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #

info "Preflight checks..."

if [ ! -f "$AGENT_ROOT/pyproject.toml" ]; then
  error "nonoka-agent repo not found at $AGENT_ROOT"
  error "Set NONOKA_AGENT_ROOT to the correct path."
  exit 1
fi

if [ ! -f "$PROVIDER_ROOT/package.json" ]; then
  error "nonoka-opencode-provider not found at $PROVIDER_ROOT"
  exit 1
fi

for cmd in python3 uv; do
  if ! command_exists "$cmd"; then
    error "$cmd is required."
    exit 1
  fi
done

if [ "$SKIP_PYPI" = false ] && ! command_exists twine; then
  error "twine is required for PyPI uploads."
  exit 1
fi

if [ "$SKIP_NPM" = false ] && ! command_exists npm; then
  error "npm is required for npm publishing."
  exit 1
fi

# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

build_python() {
  local repo="$1"
  local name="$2"
  info "Building $name at $repo..."
  (
    cd "$repo"
    rm -rf dist
    uvx --from build pyproject-build --sdist --wheel
  )
}

build_python "$AGENT_ROOT" "nonoka-agent"
build_python "$REPO_ROOT" "nonoka-cli"

info "Building nonoka-opencode-provider..."
(
  cd "$PROVIDER_ROOT"
  rm -rf dist node_modules bun.lock
  bun install
  bun run build
)

# --------------------------------------------------------------------------- #
# Local smoke test
# --------------------------------------------------------------------------- #

SMOKE_DIR="$(mktemp -d -t nonoka-release-smoke-XXXXXX)"
trap 'rm -rf "$SMOKE_DIR"' EXIT

info "Running local smoke test in $SMOKE_DIR..."
(
  cd "$SMOKE_DIR"
  uv venv --python python3.10 --seed .venv
  # shellcheck source=/dev/null
  . .venv/bin/activate
  uv pip install "$AGENT_ROOT"/dist/nonoka-*.whl "$REPO_ROOT"/dist/nonoka_cli-*.whl

  # Load the same .env files nonoka-cli loads for the API-key check.
  if [ -f "$HOME/.config/nonoka/.env" ]; then
    # shellcheck source=/dev/null
    . "$HOME/.config/nonoka/.env"
  fi

  # Verify the CLI can start and reach the LLM.
  if [ -z "${DEEPSEEK_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
    warn "No API key set; skipping LLM smoke test."
  else
    ./.venv/bin/nonoka-cli doctor --check-llm
  fi
)

if [ "$DRY_RUN" = true ]; then
  info "Dry run complete. Artifacts built but not published."
  info "  - $AGENT_ROOT/dist/"
  info "  - $REPO_ROOT/dist/"
  info "  - $PROVIDER_ROOT/dist/"
  exit 0
fi

# --------------------------------------------------------------------------- #
# Publish to PyPI
# --------------------------------------------------------------------------- #

if [ "$SKIP_PYPI" = false ]; then
  info "Uploading nonoka to PyPI ($PYPI_REPOSITORY)..."
  (
    cd "$AGENT_ROOT"
    uv run twine upload --repository "$PYPI_REPOSITORY" dist/nonoka-*
  )

  info "Uploading nonoka-cli to PyPI ($PYPI_REPOSITORY)..."
  (
    cd "$REPO_ROOT"
    uv run twine upload --repository "$PYPI_REPOSITORY" dist/nonoka_cli-*
  )
fi

# --------------------------------------------------------------------------- #
# Publish to npm
# --------------------------------------------------------------------------- #

if [ "$SKIP_NPM" = false ]; then
  info "Publishing nonoka-opencode-provider to npm..."
  (
    cd "$PROVIDER_ROOT"
    npm publish
  )
fi

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

info "Release complete!"
if [ "$SKIP_PYPI" = false ]; then
  info "  - PyPI: nonoka / nonoka-cli"
fi
if [ "$SKIP_NPM" = false ]; then
  info "  - npm:  nonoka-opencode-provider"
fi
