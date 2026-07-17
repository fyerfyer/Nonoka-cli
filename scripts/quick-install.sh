#!/usr/bin/env bash
#
# Quick installer for nonoka + OpenCode.
#
#   curl -fsSL https://nonoka.dev/install.sh | bash -s -- --quick
#
# This is a thin wrapper around install.sh with defaults tuned for an
# unattended, project-level setup:
#   - Uses uv when available
#   - Non-interactive mode
#   - Installs the OpenCode provider locally in the current directory
#   - Runs a "hello" smoke test if an API key is available
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# When run from the repo, use the local install.sh; otherwise fetch from the web.
if [ -f "$SCRIPT_DIR/install.sh" ]; then
  bash "$SCRIPT_DIR/install.sh" --uv --yes --smoke "$@"
else
  curl -fsSL https://nonoka.dev/install.sh | bash -s -- --uv --yes --smoke "$@"
fi
