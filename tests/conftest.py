"""Shared test fixtures for nonoka-cli."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.config.loader import ConfigLoader

# Load .env for tests that need real LLM access
_project_root = Path(__file__).parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
  load_dotenv(_env_file)


@pytest.fixture
def temp_config_file():
  """Yield a temporary config file path, cleaned up after test."""
  with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def sample_config() -> CLIConfig:
  """Return a sample CLIConfig for testing."""
  return CLIConfig(
    model="gpt-4o",
    system_prompt="You are a test assistant.",
  )


@pytest.fixture
def valid_config_yaml() -> str:
  """Return a valid config YAML string."""
  return """
model: gpt-4o
system_prompt: |
  You are a helpful assistant.
cli:
  theme: dark
  max_history: 500
hitl:
  policy: interactive
  dangerous_tools:
    - write_file
"""


@pytest.fixture
def config_with_env_vars() -> str:
  """Return a config YAML with environment variable references."""
  return """
model: "${NONOKA_TEST_MODEL:-gpt-4o}"
system_prompt: |
  Hello ${NONOKA_TEST_USER:-world}
cli:
  editor: "${NONOKA_TEST_EDITOR:-vim}"
"""


@pytest.fixture
def config_loader() -> ConfigLoader:
  """Return a ConfigLoader instance."""
  return ConfigLoader()
