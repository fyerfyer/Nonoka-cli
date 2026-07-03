#!/usr/bin/env bash
#
# One-line installer for nonoka + OpenCode.
#
#   curl -fsSL https://nonoka.dev/install.sh | bash
#   curl -fsSL https://nonoka.dev/install.sh | bash -s -- --uv --yes
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration defaults
# --------------------------------------------------------------------------- #

YES=false
USE_UV=false
DEV_MODE=false
NO_OPENCODE=false
OPENCODE_INSTALLER="curl"   # "curl" | "npm"
OPENCODE_VERSION=""
CLI_VERSION=""
PROVIDER_VERSION=""

NONOKA_CONFIG_DIR="${HOME}/.config/nonoka"
NONOKA_CONFIG_PATH="${NONOKA_CONFIG_DIR}/config.yaml"
OPENCODE_CONFIG_DIR="${HOME}/.config/opencode"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

info() {
  printf '\033[1;34m==>\033[0m %s\n' "$1"
}

warn() {
  printf '\033[1;33m==>\033[0m %s\n' "$1" >&2
}

error() {
  printf '\033[1;31m==>\033[0m %s\n' "$1" >&2
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

python_version_ok() {
  local version
  version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  printf '%s' "$version"
}

version_ge() {
  # Returns 0 if $1 >= $2 (simple semver comparison)
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$2" ]
}

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)
      YES=true
      shift
      ;;
    --uv)
      USE_UV=true
      shift
      ;;
    --dev)
      DEV_MODE=true
      shift
      ;;
    --no-opencode)
      NO_OPENCODE=true
      shift
      ;;
    --npm-opencode)
      OPENCODE_INSTALLER="npm"
      shift
      ;;
    --curl-opencode)
      OPENCODE_INSTALLER="curl"
      shift
      ;;
    --opencode-version)
      OPENCODE_VERSION="${2:-}"
      shift 2
      ;;
    --cli-version)
      CLI_VERSION="${2:-}"
      shift 2
      ;;
    --provider-version)
      PROVIDER_VERSION="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install.sh [OPTIONS]

Options:
  -y, --yes              Non-interactive mode
      --uv               Use uv to install nonoka-cli (falls back to pip)
      --dev              Install nonoka-cli from the local repo (implies --uv if uv is present)
      --no-opencode      Skip installing OpenCode
      --npm-opencode     Install OpenCode via npm instead of the official curl installer
      --curl-opencode    Install OpenCode via the official curl installer (default)
      --opencode-version VER  Install a specific opencode-ai version (only with --npm-opencode)
      --cli-version VER  Install a specific nonoka-cli version
      --provider-version VER  Install a specific nonoka-opencode-provider version
  -h, --help             Show this help message
EOF
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# --------------------------------------------------------------------------- #
# Preflight checks
# --------------------------------------------------------------------------- #

info "Checking prerequisites..."

if ! command_exists python3; then
  error "python3 is required but not found."
  error "Install Python 3.10+ and try again: https://www.python.org/downloads/"
  exit 1
fi

py_version=$(python_version_ok)
if ! version_ge "$py_version" "3.10"; then
  error "Python 3.10+ is required; found $py_version"
  exit 1
fi
info "Python $py_version OK"

if ! command_exists curl; then
  error "curl is required to run this installer."
  exit 1
fi

# --------------------------------------------------------------------------- #
# OpenCode
# --------------------------------------------------------------------------- #

install_opencode_curl() {
  info "Installing OpenCode via official installer..."
  curl -fsSL https://opencode.ai/install | bash
  # Make the freshly installed binary available in this shell.
  if [ -d "${HOME}/.opencode/bin" ]; then
    export PATH="${HOME}/.opencode/bin:${PATH}"
  fi
}

install_opencode_npm() {
  info "Installing OpenCode via npm..."
  npm install -g "opencode-ai${OPENCODE_VERSION:+@$OPENCODE_VERSION}"
}

if [ "$NO_OPENCODE" = false ]; then
  if command_exists opencode; then
    info "OpenCode already installed: $(opencode --version 2>/dev/null || true)"
  else
    case "$OPENCODE_INSTALLER" in
      npm)
        if command_exists npm; then
          install_opencode_npm
        else
          warn "npm not found; falling back to curl installer"
          install_opencode_curl
        fi
        ;;
      *)
        install_opencode_curl
        ;;
    esac
  fi

  if command_exists opencode; then
    info "OpenCode version: $(opencode --version 2>/dev/null || true)"
  else
    warn "OpenCode binary is not on PATH. You may need to open a new shell or add ~/.opencode/bin to PATH."
  fi
else
  info "Skipping OpenCode installation (--no-opencode)."
fi

# --------------------------------------------------------------------------- #
# nonoka-cli
# --------------------------------------------------------------------------- #

install_nonoka_cli() {
  local pkg_spec="nonoka-cli"
  if [ -n "$CLI_VERSION" ]; then
    pkg_spec="nonoka-cli==$CLI_VERSION"
  fi

  if [ "$DEV_MODE" = true ]; then
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd 2>/dev/null)" || true
    if [ -z "$script_dir" ] || [ ! -f "$script_dir/pyproject.toml" ]; then
      error "--dev requires running install.sh from the nonoka-cli repo root."
      exit 1
    fi
    info "Installing nonoka-cli in editable mode from $script_dir"
    if [ "$USE_UV" = true ] && command_exists uv; then
      uv pip install -e "$script_dir"
    else
      pip install -e "$script_dir"
    fi
    return
  fi

  if [ "$USE_UV" = true ]; then
    if command_exists uv; then
      info "Installing $pkg_spec with uv..."
      uv pip install --upgrade "$pkg_spec"
    else
      warn "uv not found; falling back to pip"
      pip install --upgrade "$pkg_spec"
    fi
  else
    info "Installing $pkg_spec with pip..."
    pip install --upgrade "$pkg_spec"
  fi
}

install_nonoka_cli

if ! command_exists nonoka-cli; then
  warn "nonoka-cli is not on PATH. If you installed with --user, ensure ~/.local/bin is in PATH."
fi

# --------------------------------------------------------------------------- #
# nonoka-opencode-provider
# --------------------------------------------------------------------------- #

install_provider() {
  if ! command_exists npm; then
    warn "npm not found; skipping global provider install (OpenCode will install it on first run)."
    return
  fi

  local pkg_spec="nonoka-opencode-provider"
  if [ -n "$PROVIDER_VERSION" ]; then
    pkg_spec="nonoka-opencode-provider@$PROVIDER_VERSION"
  fi

  info "Installing $pkg_spec globally..."
  if npm install -g "$pkg_spec"; then
    info "Provider installed."
  else
    warn "Failed to install provider globally. OpenCode can install it automatically on first run."
  fi
}

install_provider

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

info "Generating default nonoka configuration..."
mkdir -p "$NONOKA_CONFIG_DIR"

if command_exists nonoka-cli; then
  if [ "$YES" = true ]; then
    nonoka-cli config init --yes --config "$NONOKA_CONFIG_PATH"
  else
    nonoka-cli config init --config "$NONOKA_CONFIG_PATH"
  fi

  info "Generating OpenCode configuration..."
  nonoka-cli opencode init --global --config "$NONOKA_CONFIG_PATH"
else
  warn "nonoka-cli not found in PATH; skipping configuration generation."
fi

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

info "Installation complete!"
cat <<EOF

Next steps:
  1. Export your model API key, e.g.:
       export DEEPSEEK_API_KEY=<your-key>
  2. Run diagnostics:
       nonoka-cli doctor
  3. Start OpenCode:
       opencode

If [1mopencode[0m or [1mnonoka-cli[0m are not found, open a new shell or add the
following directories to your PATH:
  - ~/.opencode/bin
  - ~/.local/bin

EOF
