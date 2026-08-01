#!/usr/bin/env bash
#
# One-line installer for nonoka + OpenCode.
#
#   curl -fsSL https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/fyerfyer/Nonoka-cli/main/install.sh | bash -s -- --yes
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Configuration defaults
# --------------------------------------------------------------------------- #

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

YES=false
# Prefer uv end-to-end when it is available.  ``uv venv`` does not seed pip by
# default, so mixing it with an unqualified ``pip`` can accidentally install
# into the user's system environment instead of the new Nonoka environment.
USE_UV=true
DEV_MODE=false
NO_OPENCODE=false
GLOBAL_OPENCODE=false
SMOKE=false
VERIFY=false
LOCAL_DIST_DIR=""
LOCAL_DIST_AGENT_DIR=""
OPENCODE_INSTALLER="curl"   # "curl" | "npm"
OPENCODE_VERSION=""
CLI_VERSION=""
PROVIDER_VERSION=""

# Installation layout. Every value can be supplied as an environment variable
# or with the matching CLI option. A leading "~/" is expanded against HOME.
#
#   NONOKA_INSTALL_DIR  Python venv and npm tools (e.g. ~/.local/share/nonoka)
#   NONOKA_CONFIG_DIR   config.yaml and .env (e.g. ~/.config/nonoka)
#   NONOKA_NPM_PREFIX   npm global prefix (e.g. ~/.local/share/nonoka/npm)
INSTALL_DIR_EXPLICIT=false
if [ -n "${NONOKA_INSTALL_DIR:-}" ]; then INSTALL_DIR_EXPLICIT=true; fi

NONOKA_INSTALL_DIR="${NONOKA_INSTALL_DIR:-~/.local/share/nonoka}"
NONOKA_CONFIG_DIR="${NONOKA_CONFIG_DIR:-~/.config/nonoka}"
NONOKA_NPM_PREFIX="${NONOKA_NPM_PREFIX:-}"
NONOKA_PYTHON_ENV=""

# uv's 30-second default is brittle for the framework's larger wheels on
# slower or proxied networks. Respect an explicit user value while giving the
# installer a more practical default.
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"

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

expand_user_path() {
  case "$1" in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$PWD" "$1" ;;
  esac
}

read_path() {
  local prompt="$1" default_value="$2" value
  printf '%s [%s]: ' "$prompt" "$default_value" >&2
  # A curl | bash installation uses stdin for the script itself. Read answers
  # from the controlling terminal when one exists, while retaining stdin as a
  # fallback for downloaded scripts, redirected input, and automated tests.
  if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
    if ! IFS= read -r value </dev/tty; then
      value=""
    fi
  elif ! IFS= read -r value; then
    value=""
  fi
  printf '%s' "${value:-$default_value}"
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
    --pip)
      USE_UV=false
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
    --global-opencode)
      GLOBAL_OPENCODE=true
      shift
      ;;
    --smoke)
      SMOKE=true
      shift
      ;;
    --verify)
      VERIFY=true
      shift
      ;;
    --local-dist)
      LOCAL_DIST_DIR="${2:-}"
      if [ -z "$LOCAL_DIST_DIR" ] || [ ! -d "$LOCAL_DIST_DIR" ]; then
        error "--local-dist requires an existing directory containing nonoka_cli-*.whl"
        exit 1
      fi
      # Also look for the matching nonoka-agent wheel next to this repo.
      if [ -d "$SCRIPT_DIR/../nonoka-agent/dist" ]; then
        LOCAL_DIST_AGENT_DIR="$SCRIPT_DIR/../nonoka-agent/dist"
      fi
      shift 2
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
    --install-dir)
      NONOKA_INSTALL_DIR="${2:-}"
      if [ -z "$NONOKA_INSTALL_DIR" ]; then
        error "--install-dir requires a directory"
        exit 1
      fi
      INSTALL_DIR_EXPLICIT=true
      shift 2
      ;;
    --config-dir)
      NONOKA_CONFIG_DIR="${2:-}"
      if [ -z "$NONOKA_CONFIG_DIR" ]; then
        error "--config-dir requires a directory"
        exit 1
      fi
      shift 2
      ;;
    --npm-prefix)
      NONOKA_NPM_PREFIX="${2:-}"
      if [ -z "$NONOKA_NPM_PREFIX" ]; then
        error "--npm-prefix requires a directory"
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install.sh [OPTIONS]

Options:
  -y, --yes              Non-interactive mode
      --uv               Prefer uv for the venv and Python packages (default)
      --pip              Use venv's pip instead of uv
      --dev              Install nonoka-cli from the local repo
      --local-dist DIR   Install nonoka-agent and nonoka-cli from local wheel files in DIR
      --no-opencode      Skip installing OpenCode
      --global-opencode  Write OpenCode config to ~/.config/opencode (default: ./opencode.json)
      --smoke            Run a quick "hello" smoke test after setup (requires API key)
      --verify           Run nonoka-cli doctor --check-llm after setup (requires API key)
      --npm-opencode     Install OpenCode via npm instead of the official curl installer
      --curl-opencode    Install OpenCode via the official curl installer (default)
      --opencode-version VER  Install a specific opencode-ai version (only with --npm-opencode)
      --cli-version VER  Install a specific nonoka-cli version
      --provider-version VER  Install a specific nonoka-opencode-provider version
      --install-dir DIR       Python venv and npm tools (default: ~/.local/share/nonoka)
      --config-dir DIR        config.yaml and .env (default: ~/.config/nonoka)
      --npm-prefix DIR        npm global prefix (default: INSTALL_DIR/npm)
  -h, --help             Show this help message

Environment variables:
  NONOKA_INSTALL_DIR     Same as --install-dir
  NONOKA_CONFIG_DIR      Same as --config-dir
  NONOKA_NPM_PREFIX      Same as --npm-prefix
EOF
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Reuse an already activated venv unless the caller explicitly selected an
# install root. This preserves existing uv/pip workflows while making a fresh
# install self-contained by default.
if [ "$INSTALL_DIR_EXPLICIT" = false ] && [ -n "${VIRTUAL_ENV:-}" ]; then
  NONOKA_PYTHON_ENV="$VIRTUAL_ENV"
  NONOKA_INSTALL_DIR="$(dirname "$VIRTUAL_ENV")"
fi

if [ "$YES" = false ]; then
  install_answer=$(read_path \
    "Installation directory (Python environment, launchers, and npm tools; e.g. ~/nonoka)" \
    "$NONOKA_INSTALL_DIR")
  if [ "$install_answer" != "$NONOKA_INSTALL_DIR" ]; then
    INSTALL_DIR_EXPLICIT=true
    NONOKA_PYTHON_ENV=""
  fi
  NONOKA_INSTALL_DIR="$install_answer"

  NONOKA_CONFIG_DIR=$(read_path \
    "Configuration directory (config.yaml and .env; e.g. ~/.config/nonoka)" \
    "$NONOKA_CONFIG_DIR")
  NONOKA_NPM_PREFIX=$(read_path \
    "npm prefix (OpenCode/provider global packages)" \
    "${NONOKA_NPM_PREFIX:-${NONOKA_INSTALL_DIR}/npm}")
fi

NONOKA_INSTALL_DIR=$(expand_user_path "$NONOKA_INSTALL_DIR")
NONOKA_CONFIG_DIR=$(expand_user_path "$NONOKA_CONFIG_DIR")
if [ -z "$NONOKA_NPM_PREFIX" ]; then
  NONOKA_NPM_PREFIX="${NONOKA_INSTALL_DIR}/npm"
fi
NONOKA_NPM_PREFIX=$(expand_user_path "$NONOKA_NPM_PREFIX")
NONOKA_CONFIG_PATH="${NONOKA_CONFIG_DIR}/config.yaml"

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
# Installation layout
# --------------------------------------------------------------------------- #

prepare_install_layout() {
  mkdir -p "$NONOKA_INSTALL_DIR" "$NONOKA_CONFIG_DIR" "$NONOKA_NPM_PREFIX"

  if [ -z "$NONOKA_PYTHON_ENV" ]; then
    NONOKA_PYTHON_ENV="${NONOKA_INSTALL_DIR}/.venv"
  fi

  if [ ! -x "${NONOKA_PYTHON_ENV}/bin/python" ]; then
    info "Creating Python environment at ${NONOKA_PYTHON_ENV}..."
    if [ "$USE_UV" = true ] && command_exists uv; then
      uv venv "$NONOKA_PYTHON_ENV" --python python3
    else
      python3 -m venv "$NONOKA_PYTHON_ENV"
    fi
  else
    info "Using existing Python environment: ${NONOKA_PYTHON_ENV}"
  fi

  export VIRTUAL_ENV="$NONOKA_PYTHON_ENV"
  export NONOKA_CONFIG_DIR="$NONOKA_CONFIG_DIR"
  export NPM_CONFIG_PREFIX="$NONOKA_NPM_PREFIX"
  export PATH="${NONOKA_PYTHON_ENV}/bin:${NONOKA_NPM_PREFIX}/bin:${PATH}"

  info "Install directory: ${NONOKA_INSTALL_DIR}"
  info "Configuration directory: ${NONOKA_CONFIG_DIR}"
  info "npm prefix: ${NONOKA_NPM_PREFIX}"
}

prepare_install_layout

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

install_python_packages() {
  if [ "$USE_UV" = true ] && command_exists uv; then
    uv pip install --python "${NONOKA_PYTHON_ENV}/bin/python" "$@"
    return
  fi

  if ! "${NONOKA_PYTHON_ENV}/bin/python" -m pip --version >/dev/null 2>&1; then
    info "Bootstrapping pip in ${NONOKA_PYTHON_ENV}..."
    "${NONOKA_PYTHON_ENV}/bin/python" -m ensurepip --upgrade
  fi
  "${NONOKA_PYTHON_ENV}/bin/python" -m pip install "$@"
}

install_nonoka_cli() {
  # Local dist install: pick up nonoka-agent + nonoka-cli wheels from the
  # supplied directories. Useful for validating a release before pushing to PyPI.
  if [ -n "$LOCAL_DIST_DIR" ]; then
    local agent_whl cli_whl
    if [ -z "$LOCAL_DIST_AGENT_DIR" ] || [ ! -d "$LOCAL_DIST_AGENT_DIR" ]; then
      error "Could not find nonoka-agent wheels. Use a standard repo layout or set LOCAL_DIST_AGENT_DIR."
      exit 1
    fi
    agent_whl=$(ls -1 "$LOCAL_DIST_AGENT_DIR"/nonoka-*.whl 2>/dev/null | head -n1)
    cli_whl=$(ls -1 "$LOCAL_DIST_DIR"/nonoka_cli-*.whl 2>/dev/null | head -n1)
    if [ -z "$agent_whl" ] || [ -z "$cli_whl" ]; then
      error "Could not find nonoka-*.whl in $LOCAL_DIST_AGENT_DIR and/or nonoka_cli-*.whl in $LOCAL_DIST_DIR"
      exit 1
    fi
    info "Installing from local wheels: $agent_whl $cli_whl"
    install_python_packages "$agent_whl" "$cli_whl"
    return
  fi

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

    # In dev mode also install the local nonoka-agent core if available, so
    # that nonoka-cli uses the matching framework version.
    local agent_root
    agent_root="${NONOKA_AGENT_ROOT:-$script_dir/../nonoka-agent}"
    if [ -f "$agent_root/pyproject.toml" ]; then
      info "Installing nonoka-agent in editable mode from $agent_root"
      install_python_packages -e "$agent_root"
    fi

    info "Installing nonoka-cli in editable mode from $script_dir"
    install_python_packages -e "$script_dir"
    return
  fi

  if [ "$USE_UV" = true ] && command_exists uv; then
    info "Installing $pkg_spec with uv..."
  elif [ "$USE_UV" = true ]; then
    warn "uv not found; using the isolated environment's pip"
  else
    info "Installing $pkg_spec with pip..."
  fi
  install_python_packages --upgrade "$pkg_spec"
}

install_nonoka_cli

write_launcher() {
  local launcher_dir launcher python_env_q config_dir_q npm_prefix_q nonoka_q
  launcher_dir="${NONOKA_INSTALL_DIR}/bin"
  launcher="${launcher_dir}/nonoka"
  mkdir -p "$launcher_dir"
  printf -v python_env_q '%q' "$NONOKA_PYTHON_ENV"
  printf -v config_dir_q '%q' "$NONOKA_CONFIG_DIR"
  printf -v npm_prefix_q '%q' "$NONOKA_NPM_PREFIX"
  printf -v nonoka_q '%q' "${NONOKA_PYTHON_ENV}/bin/nonoka"
  cat > "$launcher" <<EOF
#!/usr/bin/env bash
export VIRTUAL_ENV=$python_env_q
export NONOKA_CONFIG_DIR=$config_dir_q
export NPM_CONFIG_PREFIX=$npm_prefix_q
export PATH="\${VIRTUAL_ENV}/bin:\${NPM_CONFIG_PREFIX}/bin:\${PATH}"
exec $nonoka_q "\$@"
EOF
  chmod 755 "$launcher"
  ln -sf "nonoka" "${launcher_dir}/nonoka-cli"
  info "Launcher saved to: ${launcher}"
}

write_launcher

if ! command_exists nonoka; then
  warn "nonoka is not on PATH. The launcher is still available at ${NONOKA_INSTALL_DIR}/bin/nonoka."
fi

# --------------------------------------------------------------------------- #
# nonoka-opencode-provider
# --------------------------------------------------------------------------- #

# OpenCode 1.18+ resolves custom providers from the project's node_modules.
# For project-level installs, nonoka-cli opencode init installs the provider
# locally. For global installs, we fall back to a global npm package.
install_provider() {
  if [ "$GLOBAL_OPENCODE" = false ]; then
    info "Provider will be installed locally by 'nonoka init'."
    return
  fi

  if ! command_exists npm; then
    warn "npm not found; skipping global provider install."
    return
  fi

  local pkg_spec="nonoka-opencode-provider"
  if [ -n "$PROVIDER_VERSION" ]; then
    pkg_spec="nonoka-opencode-provider@$PROVIDER_VERSION"
  fi

  info "Installing $pkg_spec globally..."
  if npm install -g "$pkg_spec"; then
    info "Provider installed globally."
  else
    warn "Failed to install provider globally. You may need to install it manually."
  fi
}

install_provider

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

info "Generating default nonoka configuration..."
mkdir -p "$NONOKA_CONFIG_DIR"

if command_exists nonoka; then
  if [ "$YES" = true ]; then
    nonoka config init --yes --config "$NONOKA_CONFIG_PATH"
  else
    nonoka config init --config "$NONOKA_CONFIG_PATH"
  fi

  info "Generating OpenCode configuration..."
  if [ "$GLOBAL_OPENCODE" = true ]; then
    nonoka init --global --config "$NONOKA_CONFIG_PATH"
  else
    nonoka init --config "$NONOKA_CONFIG_PATH"
  fi
else
  warn "nonoka not found in PATH; skipping configuration generation."
fi

# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #

run_verify() {
  if [ "$VERIFY" = false ]; then
    return
  fi

  if ! command_exists nonoka; then
    warn "nonoka not found; skipping verification."
    return
  fi

  # Load the same .env files nonoka-cli loads for the API-key check.
  if [ -f "$NONOKA_CONFIG_DIR/.env" ]; then
    # shellcheck source=/dev/null
    . "$NONOKA_CONFIG_DIR/.env"
  fi

  if [ -z "${DEEPSEEK_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
    warn "No API key found; skipping verification."
    return
  fi

  info "Running nonoka doctor --check-llm..."
  if nonoka doctor --config "$NONOKA_CONFIG_PATH" --check-llm; then
    info "Verification passed."
  else
    warn "Verification failed. Check the logs above for details."
  fi
}

run_smoke() {
  if [ "$SMOKE" = false ]; then
    return
  fi

  if ! command_exists nonoka; then
    warn "nonoka not found; skipping smoke test."
    return
  fi

  # Load the same .env files nonoka-cli loads for the API-key check.
  if [ -f "$NONOKA_CONFIG_DIR/.env" ]; then
    # shellcheck source=/dev/null
    . "$NONOKA_CONFIG_DIR/.env"
  fi

  if [ -z "${DEEPSEEK_API_KEY:-}${OPENAI_API_KEY:-}" ]; then
    warn "No API key found; skipping smoke test."
    return
  fi

  info "Running smoke test: nonoka run --message hello"
  if nonoka run --config "$NONOKA_CONFIG_PATH" --message "hello"; then
    info "Smoke test passed."
  else
    warn "Smoke test failed. Check the logs above for details."
  fi
}

run_verify
run_smoke

# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

info "Installation complete!"
printf -v launcher_q '%q' "${NONOKA_INSTALL_DIR}/bin/nonoka"
printf -v launcher_bin_q '%q' "${NONOKA_INSTALL_DIR}/bin"
printf -v config_path_q '%q' "$NONOKA_CONFIG_PATH"
cat <<EOF

Next steps:
  1. Save your model API key without exporting it in every shell:
       ${launcher_q} config init
  2. Run diagnostics:
       ${launcher_q} doctor
  3. Start OpenCode:
       ${launcher_q} run

No environment exports are required when using the launcher above.
To type 'nonoka' directly, add this one directory to PATH:
  ${launcher_bin_q}

Configuration:
  ${config_path_q}

EOF
