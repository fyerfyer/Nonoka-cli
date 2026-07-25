"""Behaviour tests for bridge-local nonoka capabilities."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nonoka_cli.bridge.nonoka_tools import nonoka__search_evidence


def _context(working_dir: Path) -> SimpleNamespace:
  return SimpleNamespace(deps=SimpleNamespace(working_dir=working_dir))


@pytest.mark.asyncio
async def test_search_evidence_reports_compact_coordinates_for_large_logical_record(tmp_path):
  target = tmp_path / "records.json"
  target.write_text('{"prefix":"' + ("x" * 80_000) + '","secret":"needle-value"}')

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": "needle-value", "path": "records.json", "context_chars": 30},
  )

  assert result["result"]["complete"] is True
  assert result["result"]["truncated"] is False
  match = result["result"]["matches"][0]
  assert match["path"] == "records.json"
  assert match["line"] == 1
  assert match["byte_offset"] > 80_000
  assert len(match["excerpt"]) < 100


@pytest.mark.asyncio
async def test_search_evidence_marks_result_limit_as_partial(tmp_path):
  (tmp_path / "values.txt").write_text("needle\nneedle\nneedle\n")

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": "needle", "max_results": 2},
  )

  evidence = result["result"]
  assert len(evidence["matches"]) == 2
  assert evidence["truncated"] is True
  assert evidence["complete"] is False


@pytest.mark.asyncio
async def test_search_evidence_supports_regex_without_returning_the_full_record(tmp_path):
  target = tmp_path / "record.json"
  target.write_text('{"payload":"' + ("x" * 80_000) + ' hf_abcdefghijklmnopqrstuvwxyz123456"}')

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": r"hf_[A-Za-z0-9]+", "path": "record.json", "mode": "regex"},
  )

  evidence = result["result"]
  assert evidence["mode"] == "regex"
  assert evidence["complete"] is True
  assert evidence["matches"][0]["match"].startswith("hf_")
  assert len(evidence["matches"][0]["excerpt"]) < 400


@pytest.mark.asyncio
async def test_literal_regex_streams_through_large_file(tmp_path):
  target = tmp_path / "large.json"
  target.write_text("{" + ("x" * (8 * 1024 * 1024 + 128)) + '"token":"needle"}')

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": "needle", "path": "large.json", "mode": "regex"},
  )

  evidence = result["result"]
  assert evidence["complete"] is True
  assert evidence["matches"][0]["match"] == "needle"


@pytest.mark.asyncio
async def test_complex_regex_skips_oversized_file_without_failing_bridge(tmp_path):
  target = tmp_path / "large.json"
  target.write_text("{" + ("x" * (8 * 1024 * 1024 + 128)) + '"token":"needle"}')

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": r"token\s*:\s*needle", "path": "large.json", "mode": "regex"},
  )

  evidence = result["result"]
  assert evidence["ok"] is True
  assert evidence["matches"] == []
  assert evidence["complete"] is False
  assert evidence["skipped_files"]


@pytest.mark.asyncio
async def test_search_evidence_rejects_workspace_escape(tmp_path):
  outside = tmp_path.parent / "outside.txt"
  outside.write_text("needle")

  result = await nonoka__search_evidence.invoke(
    _context(tmp_path),
    {"pattern": "needle", "path": "../outside.txt"},
  )

  assert result["result"]["ok"] is False
  assert "escapes the workspace" in result["result"]["error"]
