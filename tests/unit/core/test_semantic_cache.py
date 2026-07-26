"""Deterministic coverage for the opt-in semantic response cache."""

import subprocess

import pytest
from nonoka.core.llm import LLMResponse

from nonoka_cli.config.models import CacheConfig, CLIConfig
from nonoka_cli.core.orchestrator import Orchestrator
from nonoka_cli.core.semantic_cache import SQLiteSemanticResponseCache


@pytest.mark.asyncio
async def test_semantic_cache_returns_nearest_matching_response(tmp_path):
  cache = SQLiteSemanticResponseCache(tmp_path / "cache.sqlite3")
  await cache.put(
    [1.0, 0.0, 0.0], LLMResponse(content="cached"),
    model="test-model", scope="repo-a", variant="variant-a", ttl_seconds=60,
  )

  hit = await cache.get([0.99, 0.01, 0.0], model="test-model", scope="repo-a", variant="variant-a", threshold=0.95)
  miss = await cache.get([0.99, 0.01, 0.0], model="test-model", scope="repo-b", variant="variant-a", threshold=0.95)
  variant_miss = await cache.get([0.99, 0.01, 0.0], model="test-model", scope="repo-a", variant="variant-b", threshold=0.95)

  assert hit is not None and hit.content == "cached"
  assert hit.usage["_semantic_similarity_score"] > 0.99
  assert miss is None
  assert variant_miss is None


def _git(workspace, *args: str) -> None:
  subprocess.run(["git", *args], cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_semantic_scope_changes_with_workspace_state(tmp_path):
  workspace = tmp_path / "repo"
  workspace.mkdir()
  _git(workspace, "init")
  _git(workspace, "config", "user.email", "test@example.com")
  _git(workspace, "config", "user.name", "Test User")
  tracked = workspace / "app.py"
  tracked.write_text("value = 1\n")
  _git(workspace, "add", "app.py")
  _git(workspace, "commit", "-m", "initial")
  orchestrator = Orchestrator(config=CLIConfig(model="test-model", system_prompt="stable prompt"))

  committed_scope = orchestrator._semantic_cache_scope(workspace)
  tracked.write_text("value = 2\n")
  modified_scope = orchestrator._semantic_cache_scope(workspace)
  draft = workspace / "draft.txt"
  draft.write_text("first")
  untracked_scope = orchestrator._semantic_cache_scope(workspace)
  draft.write_text("second")
  changed_untracked_scope = orchestrator._semantic_cache_scope(workspace)

  assert committed_scope is not None
  assert committed_scope != modified_scope
  assert modified_scope != untracked_scope
  assert untracked_scope != changed_untracked_scope


def test_semantic_scope_disables_cache_outside_git_repository(tmp_path):
  orchestrator = Orchestrator(config=CLIConfig(model="test-model"))
  assert orchestrator._semantic_cache_scope(tmp_path) is None


def test_semantic_scope_resolver_tracks_edits_during_a_session(tmp_path, monkeypatch):
  workspace = tmp_path / "repo"
  workspace.mkdir()
  _git(workspace, "init")
  _git(workspace, "config", "user.email", "test@example.com")
  _git(workspace, "config", "user.name", "Test User")
  tracked = workspace / "app.py"
  tracked.write_text("value = 1\n")
  _git(workspace, "add", "app.py")
  _git(workspace, "commit", "-m", "initial")
  monkeypatch.chdir(workspace)
  config = CLIConfig(
    model="test-model",
    cache=CacheConfig(
      semantic_enabled=True, embedding_model="embedding-test", embedding_api_base="https://example.test/v1",
    ),
  )

  options = Orchestrator(config=config)._runner_cache_options()
  resolver = options["cache_namespace"]
  before = resolver()
  tracked.write_text("value = 2\n")
  after = resolver()

  assert callable(resolver)
  assert before is not None
  assert before != after
