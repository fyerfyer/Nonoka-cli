"""Offline behavioural tests for install.sh path selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install.sh"


def _command(path: Path, body: str) -> None:
  path.write_text("#!/usr/bin/env bash\nset -e\n" + body, encoding="utf-8")
  path.chmod(0o755)


def _fake_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
  fake_bin = tmp_path / "bin"
  fake_bin.mkdir()
  log = tmp_path / "calls.log"
  _command(fake_bin / "python3", "printf '3.13\\n'\n")
  _command(
    fake_bin / "uv",
    'printf "uv:%s\\n" "$*" >> "$NONOKA_TEST_LOG"\n'
    'if [ "${1:-}" = venv ]; then\n'
    '  mkdir -p "$2/bin"\n'
    '  touch "$2/bin/python"\n'
    '  chmod +x "$2/bin/python"\n'
    '  cp "$(dirname "$0")/nonoka" "$2/bin/nonoka"\n'
    'fi\n',
  )
  _command(fake_bin / "opencode", "printf '1.18.10\\n'\n")
  _command(fake_bin / "npm", 'printf "npm:%s\\n" "$*" >> "$NONOKA_TEST_LOG"\n')
  _command(fake_bin / "nonoka", 'printf "nonoka:%s\\n" "$*" >> "$NONOKA_TEST_LOG"\n')
  _command(fake_bin / "curl", "exit 0\n")

  env = os.environ.copy()
  env.update(
    {
      "HOME": str(tmp_path / "home"),
      "PATH": f"{fake_bin}:/usr/bin:/bin",
      "NONOKA_TEST_LOG": str(log),
    }
  )
  for name in (
    "VIRTUAL_ENV",
    "NONOKA_INSTALL_DIR",
    "NONOKA_CONFIG_DIR",
    "NONOKA_NPM_PREFIX",
  ):
    env.pop(name, None)
  return env, log


def _run(tmp_path: Path, *args: str, input_text: str | None = None, env=None):
  return subprocess.run(
    ["bash", str(INSTALLER), *args],
    cwd=tmp_path,
    env=env,
    input=input_text,
    text=True,
    capture_output=True,
    timeout=20,
    check=False,
  )


def test_install_dir_flags_expand_home_and_drive_all_commands(tmp_path: Path) -> None:
  env, log = _fake_environment(tmp_path)
  result = _run(
    tmp_path,
    "--yes",
    "--uv",
    "--no-opencode",
    "--install-dir",
    "~/chosen-install",
    "--config-dir",
    "~/chosen-config",
    "--npm-prefix",
    "~/chosen-npm",
    env=env,
  )

  assert result.returncode == 0, result.stderr
  home = Path(env["HOME"])
  calls = log.read_text()
  assert f"uv:venv {home}/chosen-install/.venv --python python3" in calls
  assert "uv:pip install --upgrade nonoka-cli" in calls
  assert f"nonoka:config init --yes --config {home}/chosen-config/config.yaml" in calls
  assert f"nonoka:init --config {home}/chosen-config/config.yaml" in calls
  assert f"npm prefix: {home}/chosen-npm" in result.stdout


def test_environment_variables_select_noninteractive_layout(tmp_path: Path) -> None:
  env, log = _fake_environment(tmp_path)
  env.update(
    {
      "NONOKA_INSTALL_DIR": str(tmp_path / "env-install"),
      "NONOKA_CONFIG_DIR": str(tmp_path / "env-config"),
      "NONOKA_NPM_PREFIX": str(tmp_path / "env-npm"),
    }
  )

  result = _run(tmp_path, "--yes", "--uv", "--no-opencode", env=env)

  assert result.returncode == 0, result.stderr
  calls = log.read_text()
  assert f"uv:venv {tmp_path}/env-install/.venv --python python3" in calls
  assert f"nonoka:config init --yes --config {tmp_path}/env-config/config.yaml" in calls
  assert f"npm prefix: {tmp_path}/env-npm" in result.stdout


def test_interactive_prompts_explain_each_directory(tmp_path: Path) -> None:
  env, log = _fake_environment(tmp_path)
  selected_install = tmp_path / "interactive-install"
  selected_config = tmp_path / "interactive-config"
  selected_npm = tmp_path / "interactive-npm"
  answers = f"{selected_install}\n{selected_config}\n{selected_npm}\n"

  result = _run(tmp_path, "--uv", "--no-opencode", input_text=answers, env=env)

  assert result.returncode == 0, result.stderr
  assert "Installation directory (Python environment, launchers, and npm tools" in result.stderr
  assert "Configuration directory (config.yaml and .env" in result.stderr
  assert "npm prefix (OpenCode/provider global packages)" in result.stderr
  calls = log.read_text()
  assert f"uv:venv {selected_install}/.venv --python python3" in calls
  assert f"nonoka:config init --config {selected_config}/config.yaml" in calls


def test_interactive_defaults_are_displayed_with_tilde(tmp_path: Path) -> None:
  env, _log = _fake_environment(tmp_path)

  result = _run(
    tmp_path,
    "--uv",
    "--no-opencode",
    input_text="\n\n\n",
    env=env,
  )

  assert result.returncode == 0, result.stderr
  assert "[~/.local/share/nonoka]" in result.stderr
  assert "[~/.config/nonoka]" in result.stderr
  assert "[~/.local/share/nonoka/npm]" in result.stderr


def test_launcher_preserves_paths_with_spaces_and_needs_no_exports(tmp_path: Path) -> None:
  env, _log = _fake_environment(tmp_path)
  install_dir = tmp_path / "install with spaces"
  config_dir = tmp_path / "config with spaces"
  npm_prefix = tmp_path / "npm with spaces"

  result = _run(
    tmp_path,
    "--yes",
    "--uv",
    "--no-opencode",
    "--install-dir",
    str(install_dir),
    "--config-dir",
    str(config_dir),
    "--npm-prefix",
    str(npm_prefix),
    env=env,
  )

  assert result.returncode == 0, result.stderr
  launcher = install_dir / "bin" / "nonoka"
  alias = install_dir / "bin" / "nonoka-cli"
  assert launcher.is_file()
  assert os.access(launcher, os.X_OK)
  assert alias.is_symlink()
  assert os.readlink(alias) == "nonoka"

  clean_env = env.copy()
  clean_env.pop("VIRTUAL_ENV", None)
  clean_env.pop("NONOKA_CONFIG_DIR", None)
  clean_env.pop("NPM_CONFIG_PREFIX", None)
  invoked = subprocess.run(
    [str(launcher), "doctor"],
    env=clean_env,
    text=True,
    capture_output=True,
    timeout=10,
    check=False,
  )
  assert invoked.returncode == 0, invoked.stderr
  calls = (tmp_path / "calls.log").read_text()
  assert "nonoka:doctor" in calls

  content = launcher.read_text(encoding="utf-8")
  escaped_venv = str(install_dir / ".venv").replace(" ", "\\ ")
  escaped_config = str(config_dir).replace(" ", "\\ ")
  escaped_npm = str(npm_prefix).replace(" ", "\\ ")
  assert f"export VIRTUAL_ENV={escaped_venv}" in content
  assert f"export NONOKA_CONFIG_DIR={escaped_config}" in content
  assert f"export NPM_CONFIG_PREFIX={escaped_npm}" in content
