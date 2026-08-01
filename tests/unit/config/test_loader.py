import os
import tempfile
from pathlib import Path

import pytest

from nonoka_cli.config.loader import ConfigLoader, _substitute_env_vars
from nonoka_cli.utils.errors import ConfigError, ConfigNotFoundError


def test_default_config_assets_share_configured_directory():
  assert ConfigLoader.MCP_SERVERS_PATH.parent == ConfigLoader.DEFAULT_PATH.parent


def test_substitute_env_vars_with_default():
  value = _substitute_env_vars("${UNSET_VAR:-default_value}")
  assert value == "default_value"


def test_substitute_env_vars_raises_when_missing():
  with pytest.raises(ConfigError):
    _substitute_env_vars("${DEFINITELY_MISSING_ENV_VAR_XYZ}")


def test_substitute_env_vars_set():
  os.environ["NONOKA_TEST_VAR"] = "hello"
  try:
    value = _substitute_env_vars("${NONOKA_TEST_VAR}")
    assert value == "hello"
  finally:
    del os.environ["NONOKA_TEST_VAR"]


def test_load_config_from_explicit_path():
  with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write('model: "deepseek-chat"\n')
    f.write('cli:\n')
    f.write('  theme: light\n')
    path = Path(f.name)

  try:
    config = ConfigLoader.load(path)
    assert config.model == "deepseek-chat"
    assert config.cli.theme == "light"
  finally:
    path.unlink(missing_ok=True)


def test_load_config_missing_file():
  with pytest.raises(ConfigNotFoundError):
    ConfigLoader.load("/nonexistent/path/to/config.yaml")


def test_load_config_with_env_substitution():
  os.environ["NONOKA_TEST_MODEL"] = "test-model"
  with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write('model: "${NONOKA_TEST_MODEL}"\n')
    path = Path(f.name)

  try:
    config = ConfigLoader.load(path)
    assert config.model == "test-model"
  finally:
    path.unlink(missing_ok=True)
    del os.environ["NONOKA_TEST_MODEL"]


def test_load_config_merges_mcp_sidecar(tmp_path, monkeypatch):
  main_config = tmp_path / "config.yaml"
  main_config.write_text('model: "gpt-4o"\n')

  mcp_path = tmp_path / "mcp_servers.yaml"
  mcp_path.write_text(
    'mcp_servers:\n  test-server:\n    transport: stdio\n    command: npx\n'
  )

  monkeypatch.setattr(ConfigLoader, "MCP_SERVERS_PATH", mcp_path)
  config = ConfigLoader.load(main_config)
  assert "test-server" in config.mcp_servers
  assert config.mcp_servers["test-server"].command == "npx"


def test_project_config_precedes_user_default(tmp_path, monkeypatch):
  project = tmp_path / "project"
  project.mkdir()
  project_config = project / "nonoka.yaml"
  project_config.write_text("model: project-model\n")
  global_config = tmp_path / "config.yaml"
  global_config.write_text("model: global-model\n")
  monkeypatch.setattr(ConfigLoader, "DEFAULT_PATH", global_config)

  resolved = ConfigLoader.find_config_file(search_dir=project)

  assert resolved == project_config
  assert ConfigLoader.load(search_dir=project).model == "project-model"
