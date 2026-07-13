"""Tests for RepoMapService."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonoka_cli.config.models import CLIConfig, RepoMapConfig
from nonoka_cli.core.repo_map_service import RepoMapService, build_repo_map_service


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
  repo = tmp_path / "repo"
  repo.mkdir()
  (repo / "foo.py").write_text("def hello():\n  pass\n")
  return repo


@pytest.mark.asyncio
async def test_build_system_prompt_block(sample_repo: Path) -> None:
  service = RepoMapService(working_dir=sample_repo, config=RepoMapConfig(enabled=True))
  block = await service.build_system_prompt_block()
  assert block is not None
  assert "Repository Map" in block
  assert "function hello" in block


@pytest.mark.asyncio
async def test_build_disabled_returns_none(sample_repo: Path) -> None:
  service = RepoMapService(working_dir=sample_repo, config=RepoMapConfig(enabled=False))
  block = await service.build_system_prompt_block()
  assert block is None


def test_build_repo_map_service_from_config(sample_repo: Path) -> None:
  config = CLIConfig(repo_map=RepoMapConfig(enabled=True, max_tokens=1024))
  service = build_repo_map_service(working_dir=sample_repo, config=config)
  assert service.enabled
  assert service._config.max_tokens == 1024
