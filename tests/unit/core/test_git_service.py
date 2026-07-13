"""Tests for GitService checkpoint / rollback helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from nonoka.core.agent import Agent
from nonoka.core.context import RunContext
from nonoka.core.session import Session
from nonoka_cli.config.models import CLIConfig, GitConfig
from nonoka_cli.core.git_service import GitService, build_git_service


async def _sh(cmd: str, cwd: Path) -> tuple[int, str, str]:
  proc = await asyncio.create_subprocess_shell(
    cmd,
    cwd=cwd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
  )
  stdout, stderr = await proc.communicate()
  return (
    proc.returncode,
    stdout.decode("utf-8", errors="replace").strip(),
    stderr.decode("utf-8", errors="replace").strip(),
  )


def _ctx(working_dir: Path) -> RunContext:
  agent = Agent(model="test")
  session = Session(
    session_id="test-git",
    agent=agent,
    deps=SimpleNamespace(working_dir=str(working_dir)),
  )
  return RunContext(session)


@pytest.fixture
async def git_repo(tmp_path: Path) -> Path:
  repo = tmp_path / "repo"
  repo.mkdir()
  await _sh("git init", repo)
  await _sh("git config user.email 'test@example.com'", repo)
  await _sh("git config user.name 'Test User'", repo)
  (repo / "base.txt").write_text("base")
  await _sh("git add base.txt && git commit -m 'initial'", repo)
  return repo


@pytest.mark.asyncio
async def test_is_git_repo(tmp_path: Path, git_repo: Path) -> None:
  service = GitService(working_dir=git_repo)
  assert service.is_git_repo() is True

  non_git = tmp_path / "not-a-repo"
  non_git.mkdir()
  service = GitService(working_dir=non_git)
  assert service.is_git_repo() is False


@pytest.mark.asyncio
async def test_status_summary_none_when_not_git(tmp_path: Path) -> None:
  service = GitService(working_dir=tmp_path)
  assert await service.status_summary() is None


@pytest.mark.asyncio
async def test_status_summary_returns_text_for_git(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo)
  summary = await service.status_summary()
  assert summary is not None
  assert "Status:" in summary
  assert "HEAD:" in summary


@pytest.mark.asyncio
async def test_should_checkpoint_before_only_for_write_tools(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  assert service.should_checkpoint_before("write_file") is True
  assert service.should_checkpoint_before("edit_file") is True
  assert service.should_checkpoint_before("search_and_replace") is True
  assert service.should_checkpoint_before("delete_file") is True
  assert service.should_checkpoint_before("read_file") is False
  assert service.should_checkpoint_before("bash") is False


@pytest.mark.asyncio
async def test_should_checkpoint_before_disabled_when_not_git(tmp_path: Path) -> None:
  service = GitService(working_dir=tmp_path, config=GitConfig(enabled=True, auto_checkpoint=True))
  assert service.should_checkpoint_before("write_file") is False


@pytest.mark.asyncio
async def test_should_checkpoint_before_disabled_by_config(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=False))
  assert service.should_checkpoint_before("write_file") is False


@pytest.mark.asyncio
async def test_checkpoint_before_returns_hash(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  (git_repo / "new.txt").write_text("new")

  checkpoint_hash = await service.checkpoint_before(_ctx(git_repo), "write_file")
  assert checkpoint_hash is not None
  assert len(checkpoint_hash) == 40  # SHA-1 hash length

  # Verify the checkpoint commit exists and is authored by nonoka.
  rc, out, _ = await _sh("git log -1 --format='%an %H'", git_repo)
  assert rc == 0
  assert "Test User (nonoka)" in out
  assert checkpoint_hash in out


@pytest.mark.asyncio
async def test_checkpoint_before_uses_path_argument(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  (git_repo / "target.py").write_text("x = 1")

  await service.checkpoint_before(
    _ctx(git_repo), "write_file", arguments={"path": "target.py"}
  )

  rc, out, _ = await _sh("git log -1 --format='%s'", git_repo)
  assert rc == 0
  assert "target.py" in out


@pytest.mark.asyncio
async def test_checkpoint_before_falls_back_to_tool_name_when_no_path(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  (git_repo / "x.txt").write_text("x")

  await service.checkpoint_before(_ctx(git_repo), "edit_file", arguments={})

  rc, out, _ = await _sh("git log -1 --format='%s'", git_repo)
  assert rc == 0
  # No path argument => message should not mention a specific file.
  assert out == "nonoka checkpoint before edit_file"


@pytest.mark.asyncio
async def test_checkpoint_before_recognizes_alternative_path_keys(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  (git_repo / "file.txt").write_text("data")

  for key in ("file_path", "filePath", "file", "filename"):
    # Reset dirty state so each checkpoint can succeed.
    await _sh("git reset --hard HEAD", git_repo)
    (git_repo / "file.txt").write_text(f"data-{key}")

    await service.checkpoint_before(
      _ctx(git_repo), "write_file", arguments={key: "file.txt"}
    )

    rc, out, _ = await _sh("git log -1 --format='%s'", git_repo)
    assert rc == 0
    assert "file.txt" in out


@pytest.mark.asyncio
async def test_checkpoint_before_skipped_when_disabled(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=False))
  (git_repo / "new.txt").write_text("new")

  result = await service.checkpoint_before(_ctx(git_repo), "write_file")
  assert result is None


@pytest.mark.asyncio
async def test_rollback_last_changes_head(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, rollback_on_error=True))
  # Modify an existing tracked file so the checkpoint preserves the change.
  (git_repo / "base.txt").write_text("modified")

  checkpoint_hash = await service.checkpoint_before(_ctx(git_repo), "write_file")
  assert checkpoint_hash is not None

  head_before = await _sh("git rev-parse HEAD", git_repo)
  rollback = await service.rollback_last(_ctx(git_repo))
  head_after = await _sh("git rev-parse HEAD", git_repo)

  assert rollback is not None
  assert "Rolled back to" in rollback
  assert head_before != head_after


@pytest.mark.asyncio
async def test_rollback_last_skipped_when_disabled(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, rollback_on_error=False))
  result = await service.rollback_last(_ctx(git_repo))
  assert result is None


@pytest.mark.asyncio
async def test_checkpoint_after_tracks_new_file(git_repo: Path) -> None:
  service = GitService(working_dir=git_repo, config=GitConfig(enabled=True, auto_checkpoint=True))
  await service.checkpoint_before(_ctx(git_repo), "write_file", arguments={"path": "new.txt"})

  (git_repo / "new.txt").write_text("hello")
  after_hash = await service.checkpoint_after(
    _ctx(git_repo), "write_file", arguments={"path": "new.txt"}
  )
  assert after_hash is not None

  rc, out, _ = await _sh("git log -1 --format='%s'", git_repo)
  assert rc == 0
  assert "after write_file" in out
  assert "new.txt" in out


@pytest.mark.asyncio
async def test_rollback_last_to_hash_removes_new_file(git_repo: Path) -> None:
  service = GitService(
    working_dir=git_repo,
    config=GitConfig(enabled=True, auto_checkpoint=True, rollback_on_error=True),
  )
  before_hash = await service.checkpoint_before(
    _ctx(git_repo), "write_file", arguments={"path": "new.txt"}
  )
  assert before_hash is not None

  (git_repo / "new.txt").write_text("hello")
  await service.checkpoint_after(_ctx(git_repo), "write_file", arguments={"path": "new.txt"})

  rollback = await service.rollback_last(
    _ctx(git_repo), to_hash=before_hash, paths=["new.txt"]
  )
  assert rollback is not None
  assert "Rolled back to" in rollback
  assert not (git_repo / "new.txt").exists()


@pytest.mark.asyncio
async def test_rollback_last_without_after_removes_untracked_file(git_repo: Path) -> None:
  service = GitService(
    working_dir=git_repo,
    config=GitConfig(enabled=True, auto_checkpoint=True, rollback_on_error=True),
  )
  before_hash = await service.checkpoint_before(
    _ctx(git_repo), "write_file", arguments={"path": "new.txt"}
  )
  assert before_hash is not None

  (git_repo / "new.txt").write_text("hello")
  rollback = await service.rollback_last(
    _ctx(git_repo), to_hash=before_hash, paths=["new.txt"]
  )
  assert rollback is not None
  assert not (git_repo / "new.txt").exists()


def test_build_git_service_from_config() -> None:
  config = CLIConfig(git=GitConfig(enabled=True, auto_checkpoint=False))
  service = build_git_service(working_dir=Path("."), config=config)
  assert service.enabled is True
  assert service._config.auto_checkpoint is False
